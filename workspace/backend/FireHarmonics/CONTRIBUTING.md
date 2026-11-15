# Change the backend

[Back to README](README.md)

Use one branch for each task.
A branch keeps your work separate until it is reviewed.

## 1. Download the project once

Accept the GitHub collaborator invitation first.

```bash
git clone https://github.com/Motoqvin/FireHarmonics.git
```

Enter the downloaded folder:

```bash
cd FireHarmonics
```

Follow [Setup](docs/SETUP.md) to install and run the backend.
Use your own NASA key in your local `.env` file.

## 2. Start a task

Finish or commit your current work before switching branches.

Switch to the shared branch:

```bash
git switch main
```

Download the latest shared changes:

```bash
git pull --ff-only origin main
```

Create a branch for your task:

```bash
git switch -c add-fire-filter
```

`add-fire-filter` is an example name. Choose a new name for each task.

## 3. Make the change

Endpoints are in `FireDetectionAPP/main.py`.
Request and response models are in `FireDetectionAPP/schemas.py`.
NASA calls, jobs, statistics, imports, and storage have separate modules.
See the file map in [README](README.md#where-the-files-are).
Tests are in `tests/`.

Keep edits focused on the task.
Update [API documentation](docs/API.md) if a response or endpoint changes.

Run the checks from the project root:

```bash
python -m unittest discover -s tests -v
```

Expected result: `OK`.

## 4. Save the change in Git

See which files changed:

```bash
git status --short
```

Keep `.env`, keys, and virtual environments out of the commit.

Prepare the changed files:

```bash
git add -A
```

Review the prepared file list:

```bash
git diff --cached --stat
```

Save a commit with a short description of your change:

```bash
git commit -m "Add fire filter"
```

## 5. Upload your branch

```bash
git push -u origin HEAD
```

Open the repository on GitHub.
Create a pull request from your branch into `main`.
A pull request asks your teammate to review and combine your changes.

Explain what changed and how you checked it.
Ask your teammate to review before merging.

If Git rejects a push or pull, keep the error message and resolve it together.
Avoid force-pushing the shared `main` branch.

## Writing notes for each other

Use short sentences and familiar words.
Put one action in each step.
Show a command and its expected result together.
Keep examples small, and explain any placeholder that needs replacing.
