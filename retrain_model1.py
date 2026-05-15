"""Retrain Model 1 using real observed dwell times from git snapshots + weather features."""
import sqlite3, json, pickle, sys, io
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.model_selection import KFold
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score, median_absolute_error
from sklearn.preprocessing import LabelEncoder
import lightgbm as lgb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# =====================================================
# 1. BUILD TRAINING DATA FROM REAL OBSERVATIONS
# =====================================================
conn = sqlite3.connect("shipnext.db")

occ = pd.read_sql("""
    SELECT imo, name, vtype, berth_id,
           dwell_minutes/60.0 as dwell_hours,
           first_seen, last_seen
    FROM berth_occupancy
    WHERE is_current=0 AND dwell_minutes > 60
""", conn)
occ["imo"] = occ["imo"].astype(str)
print(f"Completed stays (all): {len(occ)}")

# Remove extreme outliers (>96h = 4 days — likely customs hold, maintenance, etc.)
outliers = occ[occ["dwell_hours"] > 96]
occ = occ[occ["dwell_hours"] <= 96].copy()
print(f"After removing {len(outliers)} outliers (>96h): {len(occ)} stays")

# DWT and route from fleet_snapshots
dwt_df = pd.read_sql("""
    SELECT DISTINCT CAST(imo AS TEXT) as imo, dwt, route_from, route_to
    FROM fleet_snapshots WHERE dwt > 0
""", conn)
dwt_map = dwt_df.groupby("imo").first()

# Loading status from fleet_snapshots
load_df = pd.read_sql("""
    SELECT DISTINCT CAST(imo AS TEXT) as imo,
           (SELECT f2.sog FROM fleet_snapshots f2 WHERE CAST(f2.imo AS TEXT)=CAST(fleet_snapshots.imo AS TEXT)
            AND f2.berth_id IS NOT NULL LIMIT 1) as sog_at_berth
    FROM fleet_snapshots WHERE berth_id IS NOT NULL
""", conn)

# =====================================================
# 2. LOAD WEATHER DATA
# =====================================================
with open("weather_data.json", "r") as f:
    weather = json.load(f)

weather_lookup = {}
for i, t in enumerate(weather["archive"]["time"]):
    weather_lookup[t[:13]] = {
        "temp": weather["archive"]["temperature_2m"][i] or 20,
        "wind_speed": weather["archive"]["wind_speed_10m"][i] or 0,
        "wind_gust": weather["archive"]["wind_gusts_10m"][i] or 0,
        "precip": weather["archive"]["precipitation"][i] or 0,
        "wave_height": weather["marine"]["wave_height"][i] if i < len(weather["marine"]["wave_height"]) else 0,
        "swell_height": weather["marine"]["swell_wave_height"][i] if i < len(weather["marine"]["swell_wave_height"]) else 0,
    }
print(f"Weather records: {len(weather_lookup)}")

# =====================================================
# 3. LOAD BERTH MAPPINGS
# =====================================================
berths_df = pd.read_csv("exports/berths.csv")
berth_to_type = dict(zip(berths_df["berth_id"], berths_df["type"]))
berth_to_draft = dict(zip(berths_df["berth_id"], pd.to_numeric(berths_df["draft_m"], errors="coerce")))
berth_to_length = dict(zip(berths_df["berth_id"], pd.to_numeric(berths_df["length_m"], errors="coerce")))
berth_to_terminal = dict(zip(berths_df["berth_id"], berths_df["terminal"]))

# =====================================================
# 4. BUILD FEATURE MATRIX
# =====================================================
global_median = occ["dwell_hours"].median()

rows = []
for _, r in occ.iterrows():
    arr_time = datetime.fromisoformat(r["first_seen"])
    arr_key = r["first_seen"][:13]

    dwt_info = dwt_map.loc[r["imo"]] if r["imo"] in dwt_map.index else None
    dwt = float(dwt_info["dwt"]) if dwt_info is not None else 10000.0
    route_from = str(dwt_info["route_from"]) if dwt_info is not None and pd.notna(dwt_info["route_from"]) else ""

    btype = berth_to_type.get(r["berth_id"], "general_cargo")
    bdraft = berth_to_draft.get(r["berth_id"], 10.0)
    blength = berth_to_length.get(r["berth_id"], 160.0)
    terminal = berth_to_terminal.get(r["berth_id"], "UNKNOWN")

    w = weather_lookup.get(arr_key, {})

    # Type-level average dwell (leave-one-out — knowable at prediction time)
    vt = r["vtype"] or "MPP"
    type_stays = occ[occ["vtype"] == vt]
    if len(type_stays) > 1:
        type_avg_loo = (type_stays["dwell_hours"].sum() - r["dwell_hours"]) / (len(type_stays) - 1)
    else:
        type_avg_loo = global_median

    # Terminal-level average dwell (leave-one-out)
    term_stays = occ[occ["berth_id"].map(berth_to_terminal) == terminal]
    if len(term_stays) > 1:
        term_avg_loo = (term_stays["dwell_hours"].sum() - r["dwell_hours"]) / (len(term_stays) - 1)
    else:
        term_avg_loo = global_median

    row = {
        "dwell_hours": r["dwell_hours"],
        "vessel_type": vt,
        "dwt": dwt,
        "berth_type": btype,
        "berth_draft_m": float(bdraft) if pd.notna(bdraft) else 10.0,
        "berth_length_m": float(blength) if pd.notna(blength) else 160.0,
        "arrival_hour": arr_time.hour,
        "arrival_dow": arr_time.weekday(),
        "is_weekend": 1 if arr_time.weekday() >= 5 else 0,
        "type_avg_dwell": type_avg_loo,
        "terminal_avg_dwell": term_avg_loo,
        # Weather
        "wind_speed": w.get("wind_speed", 0),
        "wind_gust": w.get("wind_gust", 0),
        "wave_height": w.get("wave_height", 0),
        "swell_height": w.get("swell_height", 0),
        "temperature": w.get("temp", 20),
        "precipitation": w.get("precip", 0),
        # Terminal category
        "terminal_cat": terminal[:30] if terminal else "UNKNOWN",
    }
    rows.append(row)

df = pd.DataFrame(rows)
print(f"Training samples: {len(df)}")
print(f"Target: mean={df['dwell_hours'].mean():.1f}h, median={df['dwell_hours'].median():.1f}h")

# =====================================================
# 5. ENCODE CATEGORICALS
# =====================================================
cat_cols = ["vessel_type", "berth_type", "terminal_cat"]
label_encoders = {}
for col in cat_cols:
    le = LabelEncoder()
    df[col] = le.fit_transform(df[col].astype(str))
    label_encoders[col] = le
    print(f"  {col}: {len(le.classes_)} categories -> {list(le.classes_)}")

feature_cols = [c for c in df.columns if c != "dwell_hours"]
X = df[feature_cols]
y = np.log1p(df["dwell_hours"])

# =====================================================
# 6. TRAIN WITH 5-FOLD CV
# =====================================================
kf = KFold(n_splits=5, shuffle=True, random_state=42)
preds = np.zeros(len(df))

params = {
    "objective": "regression",
    "metric": "mae",
    "verbosity": -1,
    "num_leaves": 31,
    "learning_rate": 0.05,
    "n_estimators": 500,
    "min_child_samples": 10,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
}

for fold, (tr, te) in enumerate(kf.split(X)):
    dtrain = lgb.Dataset(X.iloc[tr], label=y.iloc[tr])
    dval = lgb.Dataset(X.iloc[te], label=y.iloc[te], reference=dtrain)

    model = lgb.train(
        params, dtrain, num_boost_round=500,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
    )
    preds[te] = model.predict(X.iloc[te], num_iteration=model.best_iteration)
    print(f"  Fold {fold+1}: best_iter={model.best_iteration}")

pred_hours = np.expm1(preds)
actual_hours = df["dwell_hours"].values

mae = mean_absolute_error(actual_hours, pred_hours)
rmse = np.sqrt(mean_squared_error(actual_hours, pred_hours))
r2 = r2_score(actual_hours, pred_hours)
med_ae = median_absolute_error(actual_hours, pred_hours)
within5 = (np.abs(actual_hours - pred_hours) <= 5).mean() * 100
within10 = (np.abs(actual_hours - pred_hours) <= 10).mean() * 100
within24 = (np.abs(actual_hours - pred_hours) <= 24).mean() * 100

print()
print("=" * 60)
print("MODEL v3 RESULTS (trained on real Alexandria data)")
print("=" * 60)
print(f"  Samples:    {len(df)}")
print(f"  MAE:        {mae:.1f}h")
print(f"  RMSE:       {rmse:.1f}h")
print(f"  R2:         {r2:.3f}")
print(f"  Median AE:  {med_ae:.1f}h")
print(f"  Within 5h:  {within5:.0f}%")
print(f"  Within 10h: {within10:.0f}%")
print(f"  Within 24h: {within24:.0f}%")

print()
print("COMPARISON:")
print(f"  {'Metric':12s} {'v2 (old data)':15s} {'v2 on live':15s} {'v3 (retrained)':15s}")
print(f"  {'MAE':12s} {'7.9h':15s} {'17.0h':15s} {f'{mae:.1f}h':15s}")
print(f"  {'R2':12s} {'0.500':15s} {'0.086':15s} {f'{r2:.3f}':15s}")
print(f"  {'MedAE':12s} {'2.3h':15s} {'5.4h':15s} {f'{med_ae:.1f}h':15s}")

# =====================================================
# 7. TRAIN FINAL MODEL ON ALL DATA
# =====================================================
final_model = lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=300)

imp = pd.Series(final_model.feature_importance(importance_type="gain"), index=feature_cols)
print("\nFeature Importance (top 15):")
for feat, val in imp.sort_values(ascending=False).head(15).items():
    print(f"  {feat:25s} {val:8.0f}")

# =====================================================
# 8. SAVE MODEL ARTIFACT
# =====================================================
artifact = {
    "model": final_model,
    "label_encoders": label_encoders,
    "feature_cols": feature_cols,
    "cat_cols": cat_cols,
    "log_transform": True,
    "metrics": {"mae": mae, "rmse": rmse, "r2": r2, "median_ae": med_ae,
                "within_5h": within5, "within_10h": within10},
    "training_samples": len(df),
    "berth_mappings": {
        "berth_to_type": berth_to_type,
        "berth_to_draft": {k: float(v) if pd.notna(v) else 10.0 for k, v in berth_to_draft.items()},
        "berth_to_length": {k: float(v) if pd.notna(v) else 160.0 for k, v in berth_to_length.items()},
    },
    "type_avg_dwell": occ.groupby("vtype")["dwell_hours"].mean().to_dict(),
    "terminal_avg_dwell": occ.groupby(occ["berth_id"].map(berth_to_terminal))["dwell_hours"].mean().to_dict(),
    "global_median_dwell": float(global_median),
    "weather_features": True,
    "data_source": "real_observations_from_648_git_snapshots",
    "date_range": f'{occ["first_seen"].min()[:10]} to {occ["last_seen"].max()[:10]}',
}

with open("../demo/handling_time_model_v3.pkl", "wb") as f:
    pickle.dump(artifact, f)
print("\nModel v3 saved to handling_time_model_v3.pkl")

conn.close()
