import os
import io
import re
import json
import zipfile
import tempfile
import webbrowser
import threading
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, send_file
from markitdown import MarkItDown
from semantic_processor import enhance_markdown_semantics

app = Flask(__name__, template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB upload limit

# Path for persistent config
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def get_default_output_dir():
    downloads = os.path.join(os.path.expanduser("~"), "Downloads", "MarkItDown_Converted")
    return downloads

def load_config():
    default_dir = get_default_output_dir()
    default_cfg = {
        "output_dir": default_dir,
        "auto_save": True,
        "semantic_mode": True,
        "combined_filename": "combined_documents.md",
        "include_toc": True
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update({k: v for k, v in data.items() if v is not None})
                return default_cfg
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

# Initialize MarkItDown instance once
md = MarkItDown()

def combine_markdown_files(files_data, include_toc=True, title="Combined Documents"):
    """
    Combines multiple converted markdown documents into a single document.
    files_data is a list of dicts: [{'filename': ..., 'content': ...}, ...]
    """
    if not files_data:
        return ""

    parts = []
    if include_toc and len(files_data) > 1:
        toc_lines = [f"# {title}", "", "## Table of Contents", ""]
        for i, item in enumerate(files_data, 1):
            name = item.get("filename", f"Document {i}")
            anchor = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")
            toc_lines.append(f"{i}. [{name}](#document-{anchor})")
        parts.append("\n".join(toc_lines))
        parts.append("---")

    for i, item in enumerate(files_data):
        name = item.get("filename", f"Document {i+1}")
        content = item.get("content", "").strip()
        anchor = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.lower()).strip("-")

        doc_section = f'<a id="document-{anchor}"></a>\n# Document: {name}\n\n{content}'
        parts.append(doc_section)
        if i < len(files_data) - 1:
            parts.append("---")

    return "\n\n".join(parts).strip() + "\n"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config", methods=["GET", "POST"])
def config_endpoint():
    if request.method == "POST":
        data = request.get_json() or {}
        cfg = load_config()
        if "output_dir" in data:
            cfg["output_dir"] = os.path.normpath(data["output_dir"].strip())
        if "auto_save" in data:
            cfg["auto_save"] = bool(data["auto_save"])
        if "semantic_mode" in data:
            cfg["semantic_mode"] = bool(data["semantic_mode"])
        if "combined_filename" in data and data["combined_filename"].strip():
            cfg["combined_filename"] = data["combined_filename"].strip()
        if "include_toc" in data:
            cfg["include_toc"] = bool(data["include_toc"])
        save_config(cfg)
        return jsonify({"success": True, "config": cfg})
    else:
        return jsonify(load_config())

@app.route("/api/browse-folder", methods=["POST"])
def browse_folder():
    """Opens native Windows folder selection dialog and returns the selected path."""
    selected_folder = ""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)

        cfg = load_config()
        initial_dir = cfg.get("output_dir", "")
        if not os.path.exists(initial_dir):
            initial_dir = os.path.expanduser("~")

        selected_folder = filedialog.askdirectory(
            initialdir=initial_dir,
            title="Select Output Directory for Converted Files"
        )
        root.destroy()
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    if selected_folder:
        selected_folder = os.path.normpath(selected_folder)
        cfg = load_config()
        cfg["output_dir"] = selected_folder
        save_config(cfg)
        return jsonify({"success": True, "folder": selected_folder})
    else:
        return jsonify({"success": False, "cancelled": True})

@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    """Opens the output folder in Windows File Explorer."""
    data = request.get_json() or {}
    folder = data.get("output_dir") or load_config().get("output_dir")
    if not folder:
        return jsonify({"success": False, "error": "No directory specified"}), 400

    folder = os.path.normpath(folder)
    try:
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        os.startfile(folder)
        return jsonify({"success": True, "folder": folder})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/convert", methods=["POST"])
def convert_file():
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400

    raw_filename = os.path.basename(uploaded_file.filename)
    clean_filename = secure_filename(raw_filename) or "converted_file"
    _, ext = os.path.splitext(clean_filename)

    # Check auto-save and semantic mode settings
    target_dir = request.form.get("output_dir")
    auto_save = request.form.get("auto_save") == "true"
    semantic_mode = request.form.get("semantic_mode", "true") == "true"

    tmp_path = None
    try:
        # Save uploaded file to temp path with original extension
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            uploaded_file.save(tmp_path)

        # Convert using MarkItDown
        result = md.convert(tmp_path)
        markdown_text = result.markdown

        # Apply AI Semantic Enhancements if enabled
        if semantic_mode:
            markdown_text = enhance_markdown_semantics(markdown_text, pdf_path=tmp_path)

        base_name = os.path.splitext(raw_filename)[0]
        md_filename = f"{base_name}.md"
        saved_path = None

        # If auto-save is enabled, write directly to user's output directory
        if auto_save and target_dir:
            try:
                target_dir = os.path.normpath(target_dir)
                os.makedirs(target_dir, exist_ok=True)
                full_save_path = os.path.join(target_dir, md_filename)
                with open(full_save_path, "w", encoding="utf-8") as f:
                    f.write(markdown_text)
                saved_path = full_save_path
            except Exception as save_err:
                print(f"Warning: Failed to auto-save to {target_dir}: {save_err}")

        return jsonify({
            "success": True,
            "markdown": markdown_text,
            "original_filename": raw_filename,
            "markdown_filename": md_filename,
            "saved_path": saved_path
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

@app.route("/api/save-files", methods=["POST"])
def save_files_to_disk():
    """Saves multiple converted files to the chosen local directory as individual .md files."""
    data = request.get_json() or {}
    files = data.get("files", [])
    target_dir = data.get("output_dir") or load_config().get("output_dir")

    if not target_dir:
        return jsonify({"success": False, "error": "No output directory specified"}), 400

    target_dir = os.path.normpath(target_dir)
    try:
        os.makedirs(target_dir, exist_ok=True)
        saved_paths = []
        for item in files:
            filename = os.path.basename(item.get("filename", "converted.md"))
            content = item.get("content", "")
            save_path = os.path.join(target_dir, filename)
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(content)
            saved_paths.append(save_path)

        return jsonify({
            "success": True,
            "saved_count": len(saved_paths),
            "output_dir": target_dir,
            "paths": saved_paths
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/save-combined", methods=["POST"])
def save_combined():
    """Combines multiple converted files into a single .md file and saves to local directory."""
    data = request.get_json() or {}
    files = data.get("files", [])
    if not files:
        return jsonify({"success": False, "error": "No files to combine"}), 400

    target_dir = data.get("output_dir") or load_config().get("output_dir")
    if not target_dir:
        return jsonify({"success": False, "error": "No output directory specified"}), 400

    combined_filename = os.path.basename(data.get("combined_filename") or load_config().get("combined_filename") or "combined_documents.md")
    if not combined_filename.lower().endswith(".md"):
        combined_filename += ".md"

    include_toc = bool(data.get("include_toc", load_config().get("include_toc", True)))
    combined_content = combine_markdown_files(files, include_toc=include_toc)

    target_dir = os.path.normpath(target_dir)
    try:
        os.makedirs(target_dir, exist_ok=True)
        save_path = os.path.join(target_dir, combined_filename)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(combined_content)

        return jsonify({
            "success": True,
            "filename": combined_filename,
            "save_path": save_path,
            "output_dir": target_dir,
            "file_count": len(files),
            "content": combined_content
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/download-combined", methods=["POST"])
def download_combined():
    """Combines multiple converted files into a single .md file for browser download."""
    data = request.get_json() or {}
    files = data.get("files", [])
    if not files:
        return jsonify({"error": "No files to combine"}), 400

    combined_filename = os.path.basename(data.get("combined_filename") or load_config().get("combined_filename") or "combined_documents.md")
    if not combined_filename.lower().endswith(".md"):
        combined_filename += ".md"

    include_toc = bool(data.get("include_toc", load_config().get("include_toc", True)))
    combined_content = combine_markdown_files(files, include_toc=include_toc)

    buf = io.BytesIO(combined_content.encode("utf-8"))
    return send_file(
        buf,
        mimetype="text/markdown; charset=utf-8",
        as_attachment=True,
        download_name=combined_filename
    )

@app.route("/api/download-zip", methods=["POST"])
def download_zip():
    data = request.get_json()
    if not data or "files" not in data or not data["files"]:
        return jsonify({"error": "No files to package"}), 400

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        used_names = set()
        for item in data["files"]:
            filename = os.path.basename(item.get("filename", "converted.md"))
            content = item.get("content", "")

            # Ensure unique filenames in ZIP archive
            base, ext = os.path.splitext(filename)
            counter = 1
            unique_name = filename
            while unique_name in used_names:
                unique_name = f"{base}_{counter}{ext}"
                counter += 1
            used_names.add(unique_name)

            zip_file.writestr(unique_name, content.encode("utf-8"))

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="MarkItDown_Converted_Files.zip"
    )

def open_browser():
    webbrowser.open_new("http://127.0.0.1:5000")

if __name__ == "__main__":
    port = 5000
    print(f"\n=======================================================")
    print(f"  MarkItDown Web UI is running at http://127.0.0.1:{port}")
    print(f"  Opening your web browser automatically...")
    print(f"  Press Ctrl+C in this terminal window to stop.")
    print(f"=======================================================\n")

    # Open browser automatically after 1.2 seconds
    threading.Timer(1.2, open_browser).start()

    app.run(host="127.0.0.1", port=port, debug=False)
