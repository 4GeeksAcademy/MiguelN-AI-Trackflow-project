from pathlib import Path

from flask import Flask, send_from_directory


ROOT_DIR = Path(__file__).resolve().parent
WEBSITE_DIR = ROOT_DIR / "apps" / "website"

app = Flask(__name__, static_folder=None)


@app.get("/")
def index():
    return send_from_directory(WEBSITE_DIR, "index.html")


@app.get("/<path:asset_path>")
def static_assets(asset_path: str):
    website_candidate = WEBSITE_DIR / asset_path
    if website_candidate.is_file():
        return send_from_directory(WEBSITE_DIR, asset_path)

    root_candidate = ROOT_DIR / asset_path
    if root_candidate.is_file():
        return send_from_directory(ROOT_DIR, asset_path)

    return {"error": "Not Found", "path": asset_path}, 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5500, debug=True)