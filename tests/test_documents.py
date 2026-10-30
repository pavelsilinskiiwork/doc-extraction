import pytest

from documents import MAX_BYTES, MAX_CHARS, DocumentError, read_document
from make_samples import pdf_bytes


def test_text_file_is_decoded():
    assert read_document("a.txt", "Invoice ü\n".encode()) == "Invoice ü"


def test_pdf_text_is_extracted_per_page():
    text = read_document("a.pdf", pdf_bytes("Invoice 42\nTotal 10.00 USD"))
    assert "--- page 1 ---" in text and "Invoice 42" in text and "Total 10.00 USD" in text


@pytest.mark.parametrize(
    "name,data,message",
    [
        ("a.docx", b"x", "Only .pdf and .txt"),
        ("a.txt", b"   \n", "No text found"),
        ("a.pdf", b"not a pdf", "Could not read"),
    ],
)
def test_unusable_files_are_rejected(name, data, message):
    with pytest.raises(DocumentError, match=message):
        read_document(name, data)


# The big inputs are built inside the tests: as parametrize values pytest would put them in the
# test id, and on Windows that overflows the 32,767-character environment variable limit.
def test_file_over_size_limit_is_rejected():
    with pytest.raises(DocumentError, match="larger than 5 MB"):
        read_document("a.txt", b"x" * (MAX_BYTES + 1))


def test_text_over_character_limit_is_rejected():
    with pytest.raises(DocumentError, match="longer than"):
        read_document("a.txt", b"x" * (MAX_CHARS + 1))
