# Contributing to Knowledge Tree

We welcome contributions! Before you begin, please review the requirements
below.

## Contributor License Agreement (CLA)

All contributors **must sign the CLA** before any contribution can be merged.

- **Individuals:** Read the [Individual CLA](CLA.md) and sign by commenting on
  your pull request with:
  > I have read the CLA Document and I hereby sign the CLA
- **Organizations:** Have an authorized representative sign the
  [Corporate CLA](CLA-CORPORATE.md) by emailing openktree@gmail.com,
  then each contributor signs on their PR as above.

Our CLA bot will automatically verify your signature on every pull request.

## Getting Started

1. **Fork** the repository and create a branch from `main`
2. **Set up** your development environment — see the
   [development setup guide](https://docs.openktree.com/contributing/development-setup)
3. **Make your changes** following the project conventions in `CLAUDE.md`
4. **Run tests** before pushing:
   ```bash
   # Backend (for the package you changed)
   uv run --project libs/kt-<name> pytest libs/kt-<name>/tests/ -x -v

   # Frontend
   cd frontend && pnpm lint && pnpm type-check && pnpm test
   ```
5. **Push** your branch and open a pull request

## Pull Request Guidelines

- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
  messages (e.g., `feat(api): add endpoint`, `fix(kt-db): handle null case`)
- Ensure all CI checks pass
- Keep PRs focused — one logical change per PR
- Include tests for new functionality

## Project Architecture

For detailed architecture, code patterns, and development instructions, see
[`CLAUDE.md`](CLAUDE.md) and the
[architecture docs](https://docs.openktree.com/contributing/architecture-overview).

## Reporting Issues

Use [GitHub Issues](https://github.com/openktree/knowledge-tree/issues) with
the provided templates for bug reports and feature requests.

## License

By contributing, you agree that your contributions will be licensed under the
project's [AGPL-3.0 License](LICENSE), subject to the terms of the CLA.
