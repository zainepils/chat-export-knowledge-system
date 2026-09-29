# Privacy

ChatGPT exports can contain messages, account identifiers, filenames, uploaded files, and sensitive personal details. Semantic vectors and search indexes are derived from those messages and should be treated as private too.

By default, this project stores imported content under `runtime/`, which Git ignores. The viewer binds to `127.0.0.1`. Embeddings are generated locally after the model has been downloaded. The project does not upload export content to a hosted search service.

Before sharing screenshots or a repository, use the synthetic example export. Do not commit ZIP exports, generated conversations, attachments, indexes, vectors, logs, or local app bundles. Review `git status` and `git ls-files` before publishing.
