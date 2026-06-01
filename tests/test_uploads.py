from pathlib import Path

import pytest

from standard_document_assistant.uploads import save_uploaded_file


def test_save_uploaded_file_writes_to_thread_upload_dir() -> None:
    record = save_uploaded_file(
        original_filename="upload.md",
        content=b"# title",
        thread_id="test-upload",
        content_type="text/markdown",
    )
    assert record.virtual_path.startswith("/workspace/input/uploads/test-upload/")
    assert Path(record.host_path).exists()


def test_save_uploaded_file_rejects_sensitive_name() -> None:
    with pytest.raises(ValueError):
        save_uploaded_file(
            original_filename=".env",
            content=b"secret",
            thread_id="test-upload",
        )

