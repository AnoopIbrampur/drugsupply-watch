"""Production model: exact explanations, persistence, bands, reason text."""

import numpy as np
import pandas as pd

from rxradar.model.backtest import LOGISTIC_FEATURES
from rxradar.model.production import (
    ProductionModel,
    band,
    reason_text,
    top_reasons,
    type_context,
)


def frame(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({c: rng.poisson(0.5, n).astype(float) for c in LOGISTIC_FEATURES})
    df["hist_days_since_listed"] = np.where(rng.random(n) < 0.5, np.nan, rng.integers(7, 2000, n))
    df["px_chg_1y"] = np.where(rng.random(n) < 0.5, np.nan, rng.normal(0, 0.2, n))
    df["injectable"] = rng.random(n) < 0.3
    df["multisource_generic"] = rng.random(n) < 0.5
    logit = -6 + 1.2 * df.hist_onsets_3y + 1.0 * df.injectable
    df["y90"] = rng.random(n) < 1 / (1 + np.exp(-logit))
    df["week"] = pd.Timestamp("2020-01-06")
    df["known90"] = df["status_known"] = True
    df["in_shortage_now"] = False
    return df


def fitted():
    df = frame()
    oos = pd.DataFrame({"score": np.linspace(0.0005, 0.2, len(df)), "y90": df.y90.values})
    return ProductionModel.fit(df, pd.Timestamp("2021-01-01"), oos), df


def test_contributions_sum_to_the_model_logit():
    model, df = fitted()
    contrib = model.contributions(df)
    raw = model.raw_score(df)
    logit = np.log(raw / (1 - raw))
    assert np.allclose(contrib.sum(axis=1).values + model.intercept, logit)
    # The planted signal is what the model learned.
    assert model.coef[model.columns.index("hist_onsets_3y")] > 0


def test_save_load_roundtrip(tmp_path):
    model, df = fitted()
    model.save(tmp_path / "m.json")
    again = ProductionModel.load(tmp_path / "m.json")
    assert np.allclose(model.probability(df), again.probability(df))


def test_bands():
    assert band(0.001) == "typical"
    assert band(0.006) == "moderate"
    assert band(0.011) == "elevated"
    assert band(0.03) == "high"


def test_reasons_are_plain_sentences():
    row = pd.Series({"hist_onsets_3y": 2, "sup_n_labelers": 1, "px_chg_1y": np.log(0.6)})
    assert reason_text("hist_onsets_3y", row) == "2 new FDA-listed shortages in the past 3 years"
    assert reason_text("sup_n_labelers", row) == "only 1 company lists it in FDA's NDC directory"
    assert reason_text("px_chg_1y", row) == "retail acquisition price down 40% over the past year"
    contrib = pd.Series({"hist_onsets_3y": 1.5, "sup_n_labelers": 0.4, "px_chg_1y": -0.2})
    assert top_reasons(contrib, row) == [
        "2 new FDA-listed shortages in the past 3 years",
        "only 1 company lists it in FDA's NDC directory",
    ]  # negative contributions are never shown as reasons


def test_drug_type_is_context_not_a_reason():
    row = pd.Series({"injectable": True, "hist_onsets_3y": 1})
    contrib = pd.Series({"injectable": 2.0, "hist_onsets_3y": 0.5})
    assert top_reasons(contrib, row) == ["1 new FDA-listed shortage in the past 3 years"]
    assert top_reasons(pd.Series({"injectable": 2.0}), row) == []
    assert "injectables" in type_context(row)
