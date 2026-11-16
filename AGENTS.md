# Unit Studio publishing

Read README.md before publishing updates.

- Do not send a full update JSON, ZIP, WAV, sprite sheet, or base64 package as
  a chat/tool argument. Previous publication attempts stopped with a chat-length
  error at GitHub Create Blob. The 0.13.0 JSON alone is 21,424,144 bytes.
- Use tools/release_transport.py to prepare small files; every transport file
  is at most 16 KiB. Upload those files as individual small blobs. Never combine
  all file contents into one tool argument. A tree may contain the blob SHAs.
- Commit chunks, request, and any transport changes atomically. Only the final
  main ref update should trigger publication. Never update latest.json early.
- A release must already be built, tested, signed, and durably saved before
  publication. This repository does not contain an unpublished 0.14.0 build.
  Do not claim that a build survived another chat without opening its file.
- Preserve the release signature. Do not put signing keys, private voice
  recordings, donor details, tokens, or local user settings in this repository.
- Verify the GitHub Actions result and the published package/index before
  claiming a release is available. Verification mode does not publish a release.
- This repository's updater reads updates/latest.json. The root latest.json
  is a legacy file; changing it alone does not publish an update.
