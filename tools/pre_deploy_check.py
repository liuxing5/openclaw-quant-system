#!/usr/bin/env python3
"""Pre-deploy static check: YAML 语法、关键 Python 文件编译、策略配置 validate。"""
import ast
import pathlib
import sys

try:
    import yaml
except ImportError:
    yaml = None

FAILED = []


def check_yaml():
    if yaml is None:
        print("WARN: PyYAML 未安装，跳过 workflow YAML 解析")
        return
    for f in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
        try:
            yaml.safe_load(f.read_text(encoding="utf-8"))
            print(f"OK  {f}")
        except Exception as exc:
            FAILED.append(f"{f}: {exc}")
            print(f"ERR {f}: {exc}")


def check_python():
    for d in ("core", "strategies", "scripts"):
        for f in sorted(pathlib.Path(d).rglob("*.py")):
            try:
                ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception as exc:
                FAILED.append(f"{f}: {exc}")
                print(f"ERR {f}: {exc}")


def check_funnel_config():
    try:
        import importlib.util, sys as _sys

        spec = importlib.util.spec_from_file_location(
            "funnel_config", "strategies/funnel_strategy/funnel_config.py"
        )
        m = importlib.util.module_from_spec(spec)
        _sys.modules["funnel_config"] = m
        spec.loader.exec_module(m)
        errs = m.FunnelConfig().validate() if hasattr(m.FunnelConfig(), "validate") else []
        if errs:
            FAILED.append(f"FunnelConfig.validate: {errs}")
            print("ERR FunnelConfig:", errs)
        else:
            print("OK  FunnelConfig.validate")
    except Exception as exc:
        FAILED.append(f"FunnelConfig: {exc}")
        print("ERR FunnelConfig:", exc)


def main():
    check_yaml()
    check_python()
    check_funnel_config()
    if FAILED:
        sys.exit("pre_deploy_check failed:\n" + "\n".join(FAILED))
    print("pre_deploy_check: PASS")


if __name__ == "__main__":
    main()
