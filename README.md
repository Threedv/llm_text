# llm_text

Small Python utility to turn a local directory into one LLM-friendly text digest.

It walks a directory, prints the included file tree, and appends the content of each included text file into a single output text file.

Useful when:
- a repo is private
- you want to share project context with an LLM without pushing the repo
- you want a simple local alternative to remote ingestion tools

## What it does

- scans a local directory only
- skips common cache / build / binary files
- includes files up to a configurable size limit
- supports include / exclude glob filters
- can convert `.ipynb` notebooks into readable text
- writes to a file or stdout

## Requirements

- Python 3.10+ recommended
- no external dependencies

## Quick start

```bash
python local_dir_ingest.py /path/to/project
```

This writes `digest.txt` in the current directory.

## Basic usage

### Write digest to a file

```bash
python local_dir_ingest.py /path/to/project -o project_digest.txt
```

### Print digest to stdout

```bash
python local_dir_ingest.py /path/to/project -o -
```

### Increase max file size

```bash
python local_dir_ingest.py /path/to/project --max-file-kb 200
```

### Include only selected file types

```bash
python local_dir_ingest.py /path/to/project -i "*.py" -i "*.md"
```

### Exclude paths or file patterns

```bash
python local_dir_ingest.py /path/to/project -e "data/*" -e "*.pt"
```

### Include notebook outputs

```bash
python local_dir_ingest.py /path/to/project --include-notebook-output
```

### Follow symlinked directories

```bash
python local_dir_ingest.py /path/to/project --follow-symlinks
```

## Output format

The generated digest contains:

1. source directory summary
2. traversal stats
3. included directory tree
4. file-by-file content blocks

Example:

```txt
Directory: /path/to/project
Files analyzed: 12
Included bytes: 41,203 (40.2 KB)
...

Directory structure:
└── project/
    ├── README.md
    ├── app.py
    └── utils/
        └── helpers.py

================================================
FILE: README.md
================================================
...
```

## Notes

- default max file size is `50 KB`
- binary / media / archive files are skipped
- common directories like `.git`, `node_modules`, `dist`, `build`, and virtual envs are skipped
- if no files match, the tool still writes a valid digest with a message

## Example

```bash
python local_dir_ingest.py ~/dev/my_private_repo -i "*.py" -i "*.md" -e "data/*" -o digest.txt
```

## License

MIT
