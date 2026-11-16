# Unit Studio updates

Signed update packages are served from `updates/`; the application reads
`updates/latest.json`. Large packages must not be pasted into a chat or sent as
one GitHub connector argument.

## Publishing from small files

`tools/release_transport.py` preserves the exact bytes of an already signed
package. It reuses unchanged file records from an existing published package,
and splits new text into content-addressed chunks of at most 16 KiB. GitHub
Actions reconstructs the full package and checks its SHA-256 before atomically
committing the package and its update index. Installed clients receive the same
full signed update format as before; no application change is required.

1. Build and test the update, verify its signature with the existing updater,
   and save the signed package and its matching `latest.json` outside the chat.
2. In a fresh clone, prepare transport files:

   ```sh
   python3 tools/release_transport.py prepare \
     --base updates/0.13.0.json \
     --target /path/to/signed-update.json \
     --latest /path/to/latest.json
   ```

   Omit `--latest` for verification only. The command first reconstructs the
   target locally and checks exact byte equality. It never reads a signing key.

3. Upload the changed files under `publishing/`, each as its own small blob.
   Create a tree from their SHAs, then one commit and a guarded, non-forced
   update of `main`. With authenticated Git, a normal file-based commit/push
   also works. Never embed the combined chunks into one tool call.
4. Wait for **Publish signed update from small files** in Actions. A `publish`
   request updates the release and `updates/latest.json` together. A `verify`
   request leaves the release channel untouched. A concurrent edit of the
   index or branch causes failure instead of overwriting newer work.
5. Confirm the action succeeded and the published index, file hash, and version
   match. Do not report publication on the basis of uploaded blobs alone.

Integrity checks reject modified chunks, a changed base, a mismatched target,
an invalid index, a downgrade, or overwriting a release. Transport SHA checks
preserve signed bytes; they do not replace the updater's signature validation.

The initial request only reconstructs the existing 0.13.0 package. It does not
release 0.14.0. That unpublished build was not available when this workflow was
added.

## Tests

```sh
python3 -m unittest discover -s tools -p 'test_*.py' -v
```
