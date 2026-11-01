"""Línea de comandos.

  python -m quantflow live                      # panel + mercado en vivo (paper trading)
  python -m quantflow backtest --hours 6        # backtest con ticks reales recientes (REST)
  python -m quantflow backtest --days 3         # backtest con archivos diarios de data.binance.vision
  python -m quantflow research --days 60        # validación estadística (ajuste vs. prueba)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import load_config


def _fmt(v, kind="num"):
    if v is None:
        return "-"
    if kind == "pct":
        return f"{v:.1%}"
    if kind == "r":
        return f"{v:+.2f}R"
    return f"{v:,.2f}"


def print_report(result: dict) -> None:
    s = result["stats"]
    print("\n================ RESULTADO DEL BACKTEST ================")
    print(f"Operaciones:      {s['trades']}")
    print(f"Tasa de acierto:  {_fmt(s['win_rate'], 'pct')}")
    print(f"Esperanza:        {_fmt(s['expectancy_r'], 'r')} por operación")
    print(f"Ganancia media:   {_fmt(s['avg_win_r'], 'r')}   Pérdida media: {_fmt(s['avg_loss_r'], 'r')}")
    print(f"Profit factor:    {_fmt(s['profit_factor'])}")
    print(f"Capital final:    {_fmt(s['equity'])}  ({s['return_pct']:+.2f}%)")
    print(f"Drawdown máximo:  {s['max_drawdown_pct']:.2f}%")
    if result["by_setup"]:
        print("\nPor setup:")
        for k, v in result["by_setup"].items():
            print(f"  {k:<18} {v['trades']:>4} ops  acierto {v['win_rate']:.0%}  "
                  f"esperanza {v['expectancy_r']:+.2f}R  total {v['total_r']:+.1f}R")
    if result["signals"]:
        print("\nSeñales evaluadas / aceptadas:")
        for k, v in result["signals"].items():
            print(f"  {k:<18} {v['evaluadas']:>5} / {v['aceptadas'] or 0}")
    print("=========================================================")
    if s["trades"] < 30:
        print("Aviso: menos de 30 operaciones no es una muestra estadísticamente fiable.")


def run_research(args, cfg) -> None:
    import json

    from . import research
    from .config import _find_default_config, resolve_path

    out_dir = resolve_path(args.out_dir, cfg.base_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.dataset:
        rows = research.load_dataset(Path(args.dataset))
    else:
        config_path = args.config or str(_find_default_config())
        dataset = out_dir / f"senales_{args.days}d.csv"
        rows = research.collect(config_path, args.symbols or list(cfg.symbols), args.days, dataset)
        print(f"Dataset de señales: {dataset}")
    result = research.analyze(rows, args.train_frac, cfg.decision.min_expected_value_r)
    report = research.render_report(result, cfg.decision.min_expected_value_r)
    (out_dir / "validacion.md").write_text(report, encoding="utf-8")
    (out_dir / "validacion.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    print(report)
    print(f"\nInforme: {out_dir / 'validacion.md'}")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="quantflow", description="Order flow + decisión cuantitativa")
    parser.add_argument("--config", help="ruta a config.yaml")
    sub = parser.add_subparsers(dest="cmd")

    live = sub.add_parser("live", help="mercado en vivo con paper trading y panel web")
    live.add_argument("--port", type=int)

    bt = sub.add_parser("backtest", help="reproduce ticks históricos reales")
    bt.add_argument("--symbols", nargs="+")
    src = bt.add_mutually_exclusive_group()
    src.add_argument("--hours", type=float, default=6, help="últimas N horas vía REST (por defecto 6)")
    src.add_argument("--days", type=int, help="últimos N días completos vía data.binance.vision")
    bt.add_argument("--out", default="reports/backtest_trades.csv")
    bt.add_argument("--save-calibration", action="store_true",
                    help="guarda la calibración aprendida para usarla en vivo")

    rs = sub.add_parser("research", help="valida estadísticamente la ventaja con datos de ajuste y de prueba")
    rs.add_argument("--symbols", nargs="+")
    rs.add_argument("--days", type=int, default=30, help="últimos N días completos (por defecto 30)")
    rs.add_argument("--train-frac", type=float, default=0.6, help="fracción inicial usada para ajustar")
    rs.add_argument("--dataset", help="reanaliza un CSV de señales ya generado, sin volver a procesar ticks")
    rs.add_argument("--out-dir", default="reports")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(args.config)

    if args.cmd == "backtest":
        from .backtest import run_backtest
        result = asyncio.run(run_backtest(cfg, args.symbols or list(cfg.symbols),
                                          hours=None if args.days else args.hours, days=args.days,
                                          out_csv=args.out, save_calibration=args.save_calibration))
        print_report(result)
        if result["trades"]:
            print(f"Detalle de operaciones: {args.out}")
        return

    if args.cmd == "research":
        run_research(args, cfg)
        return

    import uvicorn

    from .server import create_app
    port = getattr(args, "port", None) or cfg.server.port
    print(f"\nPanel: http://{cfg.server.host}:{port}   Webhook: POST /webhook/tradingview\n")
    uvicorn.run(create_app(cfg), host=cfg.server.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
