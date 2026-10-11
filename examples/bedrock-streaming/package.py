"""Build the ARM64 Python 3.13 Lambda ZIP using pinned local wheels."""

import shutil
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUILD = ROOT / "build/bedrock-streaming"
WIDGET_COMMIT = "d3ac5847a09bd2e0b6b409d5bf6b78cb082782ef"


def run(*args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)


def main():
    BUILD.mkdir(parents=True, exist_ok=True)
    widget = BUILD / "wapyt"
    if not widget.exists():
        run("git", "init", widget)
        run("git", "-C", widget, "fetch", "--depth", "1",
            "https://github.com/WAwesome-AI/wa_pytincture_widgetset.git", WIDGET_COMMIT)
        run("git", "-C", widget, "checkout", "--detach", "FETCH_HEAD")
    actual = subprocess.check_output(["git", "-C", str(widget), "rev-parse", "HEAD"], text=True).strip()
    if actual != WIDGET_COMMIT:
        raise RuntimeError("Widget checkout does not match the reviewed commit")
    run("uv", "build", "--wheel", "--out-dir", BUILD / "wheels", ROOT)
    run("uv", "build", "--wheel", "--out-dir", BUILD / "wheels", widget)
    from tomllib import loads
    version = loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    framework_wheel = BUILD / "wheels" / f"pytincture-{version}-py3-none-any.whl"
    widget_wheel = BUILD / "wheels/wapyt-0.2.0.dev1-py3-none-any.whl"
    target = BUILD / "package"
    if target.exists():
        shutil.rmtree(target)
    run("uv", "pip", "install", "--target", target, "--python-version", "3.13",
        "--python-platform", "aarch64-manylinux_2_28", "--only-binary", ":all:",
        "-r", HERE / "requirements.txt", framework_wheel, widget_wheel)
    (target / "apps").mkdir()
    for name in ("service.py", "run.sh"):
        shutil.copy2(HERE / name, target / name)
    (target / "run.sh").chmod(0o755)
    for name in ("bedrock_chat.py", "chat_backend.py"):
        shutil.copy2(HERE / "apps" / name, target / "apps" / name)
    shutil.copy2(widget_wheel, target / "apps" / widget_wheel.name)
    shutil.copy2(widget_wheel, HERE / "apps" / widget_wheel.name)
    archive = BUILD / "lambda.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(target.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                output.write(path, path.relative_to(target))
    print(f"Built {archive} ({archive.stat().st_size / 1024**2:.1f} MiB)")


if __name__ == "__main__":
    main()
