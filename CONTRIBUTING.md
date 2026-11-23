# Contributing to Eve Skillpacks

Keep this repository state-today, public, and safe for anonymous installation.
Only document behavior confirmed by shipped Eve Horizon code or completed plans;
exclude roadmap behavior, private instance values, and mutating commands
presented as validation.

The project follows the Eve Horizon
[Code of Conduct](https://github.com/eve-horizon/eve-horizon/blob/main/CODE_OF_CONDUCT.md).
Sign off contributions under the
[Developer Certificate of Origin](https://developercertificate.org/) with
`git commit -s`.

Before opening a pull request, run:

```bash
python3 scripts/check-repository.py
python3 scripts/check-source-sync.py --repo . --source .eve-horizon-src
```

The source checker is read-only and must not fetch, sync an Eve project, commit,
or push. Report vulnerabilities privately as described in
[SECURITY.md](SECURITY.md).
