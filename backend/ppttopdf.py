import subprocess
import tempfile
import sys
import shutil
import os
from pathlib import Path


def find_soffice() -> str:
    """
    Locate the LibreOffice executable across platforms.
    Checks PATH first, then common install locations on Windows/Mac/Linux.
    """
    # 1. Already on PATH? (covers Linux apt/snap installs and most Mac/Windows setups)
    for name in ("soffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found

    # 2. Common Windows install paths
    windows_candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for path in windows_candidates:
        if os.path.exists(path):
            return path

    # 3. Common Mac install path
    mac_candidate = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if os.path.exists(mac_candidate):
        return mac_candidate

    # 4. Common Linux install paths not always on PATH (e.g. tarball installs,
    # some container base images) — defensive fallback beyond `which`.
    linux_candidates = [
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/opt/libreoffice/program/soffice",
    ]
    for path in linux_candidates:
        if os.path.exists(path):
            return path
    import glob
    for path in glob.glob("/opt/libreoffice*/program/soffice"):
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        "Could not find LibreOffice ('soffice'). Install it, or edit "
        "find_soffice() in this script to point at your soffice.exe / soffice path."
    )


def convert_ppt_to_pdf(input_path: str, output_dir: str = None, timeout: int = 120) -> str:
    """
    Convert a .ppt or .pptx file to PDF using LibreOffice (headless mode).

    Args:
        input_path: Path to the .ppt/.pptx file.
        output_dir: Directory to save the PDF (defaults to same folder as input).
        timeout: Seconds to wait before giving up on the conversion.

    Returns:
        Path to the generated PDF file.
    """
    input_path = Path(input_path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    output_dir = Path(output_dir).expanduser().resolve() if output_dir else input_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    soffice_bin = find_soffice()

    # Isolated user profile avoids lock conflicts / first-run hangs,
    # and lets multiple conversions run without stepping on each other.
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    profile_uri = Path(profile_dir).as_uri()  # correct on Windows and Linux/Mac

    cmd = [
        soffice_bin,
        "--headless",
        "--norestore",
        "--nologo",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to", "pdf",
        "--outdir", str(output_dir),
        str(input_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"LibreOffice conversion timed out after {timeout}s")
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"Conversion failed (exit code {result.returncode}).\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    pdf_path = output_dir / (input_path.stem + ".pdf")
    if not pdf_path.exists():
        raise RuntimeError(
            "LibreOffice ran without error but no PDF was produced.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    print(f"Saved: {pdf_path}")
    return str(pdf_path)


def convert_ppt_to_pdf_cached(input_path: str, cache_dir: str, cache_key: str,
                               timeout: int = 120) -> str:
    """
    Same as convert_ppt_to_pdf, but skips the (slow — LibreOffice startup is
    ~2-5s) conversion entirely if a cached PDF for this exact file already
    exists and is at least as new as the source. Intended for preview
    endpoints that may be hit repeatedly for the same document.

    cache_key should be something stable per source file — e.g. the
    document's doc_id — NOT derived from input_path's own filename, so
    re-uploads of a same-named-but-different file don't collide.
    """
    input_path = Path(input_path).expanduser().resolve()
    cache_dir  = Path(cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_pdf = cache_dir / f"{cache_key}.pdf"

    if cached_pdf.exists() and cached_pdf.stat().st_mtime >= input_path.stat().st_mtime:
        return str(cached_pdf)

    produced_path = convert_ppt_to_pdf(str(input_path), str(cache_dir), timeout=timeout)
    produced_path = Path(produced_path)

    # LibreOffice names the output after the SOURCE filename's stem, which
    # may not match cache_key — normalize it to the cache_key-based name so
    # repeat lookups by cache_key are a simple, deterministic path check.
    if produced_path != cached_pdf:
        if cached_pdf.exists():
            cached_pdf.unlink()
        produced_path.rename(cached_pdf)

    return str(cached_pdf)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ppt_to_pdf.py <input.pptx> [output_dir]")
        print(r'Example: python ppt_to_pdf.py "C:\Users\you\Downloads\deck.pptx"')
        sys.exit(1)

    input_file = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        convert_ppt_to_pdf(input_file, out_dir)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)