# Upload and share on GitHub

[Back to README](../README.md)

This project already has a GitHub remote:

[github.com/Motoqvin/FireHarmonics](https://github.com/Motoqvin/FireHarmonics)

You can use that repository. You do not need to create a second one.

## 1. Replace the exposed key

The current local NASA key appears in the existing Git history.
Removing it from today's files does not remove it from older commits.

Before sharing the repository further:

1. Revoke the exposed key through NASA FIRMS. Contact FIRMS support if needed.
2. Request a replacement from the [NASA key page](https://firms.modaps.eosdis.nasa.gov/api/map_key/).
3. Put the replacement in `FireDetectionAPP/.env` only.
4. Leave `.env.example` as a placeholder.
5. Restart the backend after editing `.env`.

Each teammate should use their own key.

GitHub recommends revoking or rotating an exposed secret first.
See [GitHub's guidance on exposed secrets](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

History cleanup is a separate task. It changes commit IDs and needs coordination.
These upload steps do not erase the old key from history.

## 2. Open the project folder

On the current Mac:

```bash
cd /Users/capy_lord/Documents/FireHarmonics
```

Confirm the destination:

```bash
git remote -v
```

Expected destination: `https://github.com/Motoqvin/FireHarmonics.git`.

## 3. Check the files

```bash
git status --short
```

Check that the private file is ignored:

```bash
git check-ignore FireDetectionAPP/.env
```

Expected result: `FireDetectionAPP/.env`.

Run the tests with your Python environment activated:

```bash
python -m unittest discover -s tests -v
```

Expected result: `OK`.

## 4. Save and upload the current version

These commands assume you are on `main` and have replaced the exposed key.

Prepare the changed files:

```bash
git add -A
```

Review the prepared file list. It should not include `.env` or `.venv`:

```bash
git diff --cached --stat
```

Save the version locally:

```bash
git commit -m "Add FastAPI backend and contributor documentation"
```

Upload it:

```bash
git push -u origin main
```

Refresh the GitHub repository page. The new README should appear.

GitHub may ask you to authenticate. An account password does not work for
HTTPS Git authentication. Use a supported sign-in method from
[GitHub's authentication guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github).

If the push is rejected because GitHub has newer commits, stop and reconcile
those changes. Keep the error message; do not use a force push.

These steps follow [GitHub's command-line upload guide](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).

## 5. Invite your teammate

Do this on the GitHub website as the repository owner.

1. Open the repository.
2. Select **Settings**.
3. Select **Collaborators** under **Access**.
4. Select **Add people**.
5. Find your teammate by their GitHub username.
6. Send the invitation.
7. Ask them to accept it.

They can then push branches to this repository.
These steps come from [GitHub's collaborator guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository).

## 6. Send them the starting point

Send the repository link and ask them to start with `README.md`.

For code changes, follow [Contributing](../CONTRIBUTING.md).
That guide covers branches, tests, commits, and pull requests.

Uploading code to GitHub does not run the backend online.
Each teammate starts it locally using [Setup](SETUP.md).
