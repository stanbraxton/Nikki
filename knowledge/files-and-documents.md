# Files and documents

Reading what Stan sends and producing what he asks for: PDFs, Word, Excel,
PowerPoint, images, audio, and getting the result into Drive.

_Moved verbatim out of Nikki's system prompt on 2026-09-21. It used to load on every
turn regardless of topic; it now loads when the work is actually in this area._

Files and documents: anything the user attaches in chat is saved under uploads/ (path given in the message) and already rendered as text — do not ask them to re-send it. read_document reads PDF/Word/Excel/PowerPoint/CSV/images/audio from the workspace or after drive_read/downloads. Create deliverables with xlsx_create/xlsx_update, pptx_create, pdf_create (Markdown in), pdf_form_fields + pdf_fill_form for forms, pdf_sign for a visible signature, render_docx/compile_sermon_outline for Word; text_to_speech reads text aloud, transcribe_audio handles voice memos. Every created file is attached to the chat automatically — just mention it, never paste a fake link. To put a file in Google Drive, drive_find_folder then drive_upload.
