# Contributing

Small, documented improvements and bug reports are welcome. Include a minimal
synthetic example, expected behavior and actual behavior. Never include
credentials, customer records, private configurations or proprietary source.

Run `python -m unittest discover -s tests -v` from the repository root. Add a
behavior test for changed validation rules. Keep runtime dependencies at zero,
error reports machine-readable, and examples fictional. Changes to accepted
fields must update the README and examples. Do not add network requests,
command execution, telemetry or automatic task actions.

Open a pull request with the problem, the change and the test command you ran.
Contributions are accepted under the repository's MIT license. Please be
respectful: discuss behavior and evidence, avoid personal attacks, and keep
private information out of public discussions. Maintainers may remove abusive
or sensitive material.

## Package-page checks

Before a release, build and render the actual wheel description with Python 3.11+:

```sh
python -m pip install --require-hashes -r requirements/build.txt -r requirements/docs.txt
python -m unittest discover -s scripts -p 'test_package_page.py' -v
python -m build --no-isolation
python scripts/check_package_page.py dist/*.whl --html package-description.html --online
```

Use a clean `dist/` directory containing only the candidate wheel. The CI package-page job checks the wheel/README match, Markdown rendering, anchors, absolute documentation URLs, repository file targets and installation pins. The `--online` release check also fetches every external link and fails if a destination cannot be verified. Review the saved HTML and run the README examples against the actual installed published package before uploading; repeat the link and version checks on the live PyPI page afterward. These checks require no publishing credential. Historical test results and example-output attribution should stay tied to their original version.
