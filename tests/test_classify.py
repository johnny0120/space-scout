from pathlib import Path, PureWindowsPath

from space_scout.classify import classify


def test_known_developer_paths_are_classified():
    assert classify(Path("/x/.cache/uv"), "directory", 100) == "cache"
    assert classify(Path("/x/target/debug"), "directory", 100) == "build-artifact"
    assert classify(Path("/x/.m2/repository"), "directory", 100) == "dependency-store"


def test_classification_precedence_is_deterministic():
    assert classify(Path("/x/node_modules/.cache"), "directory", 100) == "build-artifact"
    assert classify(Path("/x/.cache/models"), "directory", 100) == "model-cache"


def test_common_file_types_have_advisory_labels():
    assert classify(Path("/x/archive.tar.gz"), "file", 100) == "archive"
    assert classify(Path("/x/video.mp4"), "file", 100) == "media"
    assert classify(Path("/x/main.py"), "file", 100) == "source"
    assert classify(Path("/x/README"), "file", 100) == "file"


def test_common_download_documents_and_installers_have_labels():
    assert classify(Path("/x/brief.pdf"), "file", 100) == "document"
    assert classify(Path("/x/slides.pptx"), "file", 100) == "document"
    assert classify(Path("/x/app.dmg"), "file", 100) == "installer"
    assert classify(Path("/x/tool.jar"), "file", 100) == "installer"


def test_common_text_data_and_font_downloads_have_labels():
    assert classify(Path("/x/readme.md"), "file", 100) == "document"
    assert classify(Path("/x/payload.json"), "file", 100) == "data"
    assert classify(Path("/x/notes.txt"), "file", 100) == "document"
    assert classify(Path("/x/font.ttf"), "file", 100) == "font"
    assert classify(Path("/x/recording.m4v"), "file", 100) == "media"
    assert classify(Path("/x/diagram.svg"), "file", 100) == "media"
    assert classify(Path("/x/payload.br"), "file", 100) == "archive"


def test_fallback_classes_never_use_unknown():
    assert classify(Path("/x/unfamiliar.pos"), "file", 100) == "extension"
    assert classify(Path("/x/README"), "file", 100) == "file"
    assert classify(Path("/x/untitled-folder"), "directory", 100) == "directory"


def test_windows_system_paths_are_protected_with_drive_aware_components():
    assert classify(PureWindowsPath("C:/Windows/System32"), "directory", 100) == "system-protected"
    assert classify(PureWindowsPath("C:/Program Files/App/app.exe"), "file", 100) == "system-protected"
