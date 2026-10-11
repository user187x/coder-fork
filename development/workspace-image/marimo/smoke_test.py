# The workspace image's offline check of marimo (build-workspace-image.sh exports it with no network: every cell must
# run). It touches what marimo itself offers (SQL cells, tables, charts, formatting) and the common libraries.
import marimo

app = marimo.App()


@app.cell
def _():
    import altair as alt
    import duckdb
    import marimo as mo
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import polars as pl
    import scipy
    import sklearn
    import tkinter

    return alt, duckdb, mo, np, pd, pl, plt, scipy, sklearn, tkinter


@app.cell
def _(duckdb, mo, np, pd, pl):
    frame = pl.DataFrame({"x": np.arange(10), "y": np.arange(10) ** 2})
    total = duckdb.sql("SELECT sum(y) AS total FROM frame").fetchone()[0]
    sql_cell = mo.sql("SELECT x, y FROM frame WHERE x > 5")
    table = mo.ui.table(pd.DataFrame({"a": [1, 2, 3]}))
    return frame, total


@app.cell
def _(alt, frame, mo, plt):
    chart = mo.ui.altair_chart(alt.Chart(frame.to_pandas()).mark_line().encode(x="x", y="y"))
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    return


@app.cell
def _(sklearn, total):
    import subprocess
    import sys

    from sklearn.linear_model import LinearRegression

    LinearRegression().fit([[0], [1]], [0, 1])
    ruff = subprocess.run([sys.executable, "-m", "ruff", "--version"], capture_output=True, text=True, check=True)
    assert total == 285, total
    print("marimo offline check passed:", ruff.stdout.strip())
    return


if __name__ == "__main__":
    app.run()
