# Third-Party Notices

SysML Repo Modeler is licensed under the MIT License. Third-party dependencies used
by the application, development tooling, and container images retain their own
licenses.

This file summarizes the project dependencies that should be considered when
redistributing the source code or published Docker images. The exact resolved
frontend dependency graph is recorded in `src/ui/package-lock.json`; Python
dependency constraints are recorded in `pyproject.toml`.

## Runtime Dependencies

| Package                    | Use                                           | License       |
| -------------------------- | --------------------------------------------- | ------------- |
| React / React DOM          | UI framework and DOM rendering                | MIT           |
| `@xyflow/react`            | Interactive graph canvas                      | MIT           |
| ELK.js (`elkjs`)           | Graph layout engine                           | EPL-2.0       |
| Lucide React               | Icon library                                  | ISC           |
| Alembic                    | Database migrations                           | MIT           |
| SQLAlchemy                 | Database toolkit                              | MIT           |
| Psycopg / `psycopg-binary` | PostgreSQL adapter                            | LGPL-3.0-only |
| OpenCode (`opencode-ai`)   | Agent runtime installed in the OpenCode image | MIT           |

## Development and Build Dependencies

| Package                       | Use                           | License      |
| ----------------------------- | ----------------------------- | ------------ |
| Vite / `@vitejs/plugin-react` | Frontend build tooling        | MIT          |
| TypeScript                    | Frontend language tooling     | Apache-2.0   |
| Prettier                      | Frontend formatting           | MIT          |
| Ruff                          | Python linting and formatting | MIT          |
| pytest                        | Python test runner            | MIT          |
| pre-commit                    | Git hook runner               | MIT          |
| detect-secrets                | Secret scanning               | Apache-2.0   |
| FastAPI                       | Optional ASGI API adapter     | MIT          |
| Uvicorn                       | Optional ASGI server          | BSD-3-Clause |

## Notable Transitive Licenses

The resolved frontend dependency tree also includes packages under MIT, ISC,
BSD-3-Clause, Apache-2.0, 0BSD, MPL-2.0, and EPL-2.0 licenses.

The current notable non-MIT-style transitive family is `lightningcss`, used by
the frontend build chain, which is licensed under MPL-2.0.

## Notes for Redistributors

- Preserve third-party license notices when redistributing source or container
  images.
- Review EPL-2.0, MPL-2.0, and LGPL-3.0-only obligations before publishing
  modified versions of the relevant third-party components.
- This notice is provided for project hygiene and is not legal advice.
