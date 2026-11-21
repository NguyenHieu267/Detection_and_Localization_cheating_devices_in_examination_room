#!/usr/bin/env python3
"""Train RSSI -> (x,y) regression from RF Localization MVP CSV.

Does NOT pretend one recording session is an independent test set.
Reports held-out-Zone cross-validation when only one session exists.
Once there are >=2 independent sessions, evaluates held-out sessions instead.

Zone IDs currently use a 15-column *legacy* layout; do not renumber them.
"""
import argparse
import csv
import json
import os
import math
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.model_selection import GroupShuffleSplit
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.compose import TransformedTargetRegressor

RSSI_COLUMNS = ["ESP01 - RSSI", "ESP02 - RSSI", "ESP03 - RSSI", "ESP04 - RSSI"]
FEATURE_NAMES = (
    [f"{n}_median" for n in ("esp01", "esp02", "esp03", "esp04")]
    + [f"{n}_std" for n in ("esp01", "esp02", "esp03", "esp04")]
    + [f"{n}_observed_ratio" for n in ("esp01", "esp02", "esp03", "esp04")]
)


def zone_center(zone: str, grid_columns=15, cell_width=0.30, cell_length=0.30,
                room_width=4.30, room_length=4.20):
    """Map existing Z1.. numbering to cell centers (not ESP anchor coordinates)."""
    if not zone.startswith("Z") or not zone[1:].isdigit():
        raise ValueError(f"Invalid Zone: {zone!r}")
    idx = int(zone[1:]) - 1
    col, row = idx % grid_columns, idx // grid_columns
    x, y = (col + 0.5) * cell_width, (row + 0.5) * cell_length
    if x > room_width + 1e-7 or y > room_length + 1e-7:
        raise ValueError(f"Zone {zone} => center ({x:.2f},{y:.2f}) m outside room. "
                         "Check geometry/legacy Zone mapping before training.")
    return x, y


def load_csv(path):
    segments = []
    current = None
    observed_counts = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        need = {"session", "Zone", *RSSI_COLUMNS}
        missing = need.difference(rdr.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        for row in rdr:
            key = (row["session"].strip(), row["Zone"].strip())
            if current is None or key != current["key"]:
                current = {"key": key, "rows": []}
                segments.append(current)
            vals = []
            for col in RSSI_COLUMNS:
                v = row[col].strip()
                vals.append(float(v) if v and v.lower() not in ("null", "nan", "na") else np.nan)
            current["rows"].append(vals)
            observed_counts.append(sum(np.isfinite(vals)))
    nonempty = [count for count in observed_counts if count]
    if nonempty and max(nonempty) == 1:
        raise ValueError(
            "This CSV contains individual raw ESP32 frames, not synchronized vectors. "
            "Prepare a separate vector dataset explicitly before training; do not modify the raw CSV."
        )
    return segments


def window_features(rows):
    """Feature vector from consecutive observations of one device at one location.

    Do not make up missing RSSI (missing values are imputed by the model pipeline).
    Require at least 3 of 4 ESPs observed in the window.
    """
    a = np.asarray(rows, dtype=float)
    if a.ndim != 2 or a.shape[1] != 4:
        raise ValueError("Expect a list of [ESP01, ESP02, ESP03, ESP04] vectors")
    observed = np.isfinite(a)
    if np.count_nonzero(observed.any(axis=0)) < 3:
        return None
    med, std = [], []
    for j in range(4):
        v = a[observed[:, j], j]
        med.append(float(np.median(v)) if len(v) else np.nan)
        std.append(float(np.std(v)) if len(v) else np.nan)
    return np.asarray(med + std + observed.mean(axis=0).tolist(), dtype=float)


def build_windows(segments, window_size=5, grid_columns=15, cell_width=.3,
                  cell_length=.3, room_width=4.3, room_length=4.2):
    xs, ys, zones, sessions = [], [], [], []
    rejected = 0
    for segment in segments:
        session, run_label = segment["key"]
        zone = run_label.split("_run", 1)[0]
        xy = zone_center(zone, grid_columns, cell_width, cell_length, room_width, room_length)
        rows = segment["rows"]
        for start in range(0, len(rows) - window_size + 1, window_size):
            feats = window_features(rows[start:start + window_size])
            if feats is None:
                rejected += 1
                continue
            xs.append(feats)
            ys.append(xy)
            zones.append(zone)
            sessions.append(session)
    if not xs:
        raise ValueError("No valid RSSI windows available")
    return (np.asarray(xs, dtype=float), np.asarray(ys, dtype=float),
            np.asarray(zones), np.asarray(sessions), rejected)


def estimator(name, seed=42):
    if name == "kNN":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             KNeighborsRegressor(n_neighbors=5, weights="distance"))
    if name == "ExtraTrees":
        return make_pipeline(SimpleImputer(strategy="median"),
            ExtraTreesRegressor(n_estimators=180, min_samples_leaf=2, max_features=1.0,
                                random_state=seed, n_jobs=-1))
    if name == "RandomForest":
        return make_pipeline(SimpleImputer(strategy="median"),
            RandomForestRegressor(n_estimators=180, min_samples_leaf=2,
                                  random_state=seed, n_jobs=-1))
    if name == "MLP":
        reg = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
            MLPRegressor(hidden_layer_sizes=(64, 32), activation="relu",
                         solver="adam", alpha=2.0, learning_rate_init=.001,
                         early_stopping=True, n_iter_no_change=20,
                         max_iter=450, random_state=seed))
        return TransformedTargetRegressor(regressor=reg, transformer=StandardScaler())
    raise ValueError(name)


def metrics(actual, pred, cell_w=.3, cell_h=.3):
    e = np.linalg.norm(pred - actual, axis=1)
    # Exact-cell rate uses current 15-column legacy cell layout, for context only.
    c_true = np.floor(actual / np.array([cell_w,cell_h])).astype(int)
    c_pred = np.floor(np.maximum(pred,0) / np.array([cell_w,cell_h])).astype(int)
    return {
        "n": int(len(e)),
        "MDE_m": round(float(e.mean()), 4),
        "median_error_m": round(float(np.median(e)), 4),
        "p90_error_m": round(float(np.percentile(e,90)), 4),
        "within_0_3m_pct": round(float(np.mean(e <= .30)*100),2),
        "within_0_6m_pct": round(float(np.mean(e <= .60)*100),2),
        "within_1_0m_pct": round(float(np.mean(e <= 1.00)*100),2),
        "exact_cell_pct": round(float(np.mean(np.all(c_true == c_pred,axis=1))*100),2),
    }


def run(args):
    segments = load_csv(args.csv)
    X, y, zone, session, rejected = build_windows(
        segments, args.window, args.grid_columns, args.cell_width,
        args.cell_length, args.room_width, args.room_length)
    print(f"Valid windows: {len(X)}; zones: {len(np.unique(zone))}; "
          f"sessions: {len(np.unique(session))}; rejected: {rejected}")
    unique_sessions = list(dict.fromkeys(session.tolist()))
    if len(unique_sessions) >= 2:
        test_session = args.test_session or unique_sessions[-1]
        if test_session not in unique_sessions:
            raise ValueError(f"--test-session {test_session!r} not present in CSV")
        # Critical: a small new measurement run should be TESTED, not used
        # as the only training set (GroupShuffleSplit could reverse this).
        fixed_split = (np.flatnonzero(session != test_session),
                       np.flatnonzero(session == test_session))
        if len(fixed_split[0]) < 20 or len(fixed_split[1]) < 5:
            raise ValueError("Too few train/test windows for session holdout")
        split_kind = f"held-out independent session: {test_session}"
        seeds = [42]
    else:
        groups = zone
        fixed_split = None
        split_kind = "held-out Zone (NOT an independent new-session test)"
        seeds = [42, 2026, 2027]
    print("Split:", split_kind)
    models = ["kNN", "ExtraTrees", "RandomForest", "MLP"]
    results = {name: [] for name in models}
    for seed in seeds:
        if fixed_split is not None:
            tr, te = fixed_split
        else:
            splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
            tr, te = next(splitter.split(X, y, groups))
        print(f"  seed {seed}: train={len(tr)}, test={len(te)}, "
              f"test-zones={len(np.unique(zone[te]))}")
        for name in models:
            model = estimator(name, seed)
            model.fit(X[tr], y[tr])
            pred = np.asarray(model.predict(X[te])).reshape(-1, 2)
            score = metrics(y[te], pred, args.cell_width,args.cell_length)
            results[name].append(score)
            print(f"    {name:<12} MDE={score['MDE_m']:.3f} m "
                  f"median={score['median_error_m']:.3f} m "
                  f"p90={score['p90_error_m']:.3f} m")
    ranking = sorted(models, key=lambda name: np.mean([r["MDE_m"] for r in results[name]]))
    best = ranking[0]
    final_model = estimator(best, 42)
    final_model.fit(X, y)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model_data = {
        "model":final_model, "model_type":best, "window_size":args.window,
        "feature_names":FEATURE_NAMES,
        "grid_columns":args.grid_columns, "cell_width":args.cell_width,
        "cell_length":args.cell_length, "room_width":args.room_width,
        "room_length":args.room_length,
        "allowed_zones":sorted(set(zone), key=lambda k:int(k[1:])),
    }
    joblib.dump(model_data, out/"model.joblib",compress=3)
    summary = {
        "dataset_path":str(Path(args.csv).name),
        "rows":sum(len(s["rows"]) for s in segments),
        "windows":len(X),"zones":int(len(np.unique(zone))),
        "sessions":sorted(set(session)),"rejected_windows":rejected,
        "evaluation_type":split_kind,
        "caution": ("With one session, Zone holdout only estimates interpolation to unseen "
                    "cells and does NOT measure future-session accuracy. With multiple "
                    "sessions, the evaluation holds out an entire independent session. "
                    "The exported deployment model is subsequently retrained on all data."),
        "window_size":args.window,
        "best_by_zone_holdout_MDE":best,
        "scores_by_model":results,
        "average_MDE_m":{k:round(float(np.mean([v['MDE_m'] for v in vals])),4) for k,vals in results.items()},
    }
    (out/"metrics.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Selected model: {best}; saved: {out/'model.joblib'}")
    print("WARNING: Final model fitted on all data, no independent test yet.")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv",default=str(ROOT / os.getenv("AI_DATASET_PATH", "model/dataset-vectors.csv")))
    ap.add_argument("--output",default=str((ROOT / os.getenv("AI_MODEL_PATH", "model/output/model.joblib")).parent))
    ap.add_argument("--window",type=int,default=int(os.getenv("AI_WINDOW_SIZE", "10")))
    ap.add_argument("--test-session",default=None,help="Explicit independent session ID to hold out (default last in CSV)")
    ap.add_argument("--grid-columns",type=int,default=math.ceil(float(os.getenv("ROOM_WIDTH_M", "4.3")) / float(os.getenv("GRID_CELL_WIDTH_M", "0.3")) - 1e-9))
    ap.add_argument("--cell-width",type=float,default=float(os.getenv("GRID_CELL_WIDTH_M", "0.3")))
    ap.add_argument("--cell-length",type=float,default=float(os.getenv("GRID_CELL_LENGTH_M", "0.3")))
    ap.add_argument("--room-width",type=float,default=float(os.getenv("ROOM_WIDTH_M", "4.3")))
    ap.add_argument("--room-length",type=float,default=float(os.getenv("ROOM_LENGTH_M", "4.2")))
    run(ap.parse_args())
