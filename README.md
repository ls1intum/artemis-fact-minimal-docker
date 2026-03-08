# Artemis FACT Minimal Docker

Minimal Docker image for the [FACT](https://github.com/Sharingcodeability/FACT) (Framework for Automated C-exercise Tests) used with [Artemis](https://github.com/ls1intum/Artemis) for automated C programming exercise assessment.

Replaces `sharingcodeability/fact:latest` (2.26 GB) with a ~450 MB image — a ~5x reduction.

## Supported Architectures

- `linux/amd64`
- `linux/arm64`

## Supported FACT Test Types

- `compile` — compilation tests
- `io` — input/output tests
- `structural` — structural code analysis tests
- `grey_box` / `grey_box_c` — grey box tests

**Not supported:** `oclint` tests (OCLint is not included to keep the image minimal).

## Usage with Artemis

Use `ls1tum/artemis-fact-minimal-docker:latest` (or the GHCR equivalent `ghcr.io/ls1intum/artemis-fact-minimal-docker:latest`) as the Docker image for C programming exercises in Artemis.

## Local Build & Test

Build the image:

```bash
docker build -t fact-test .
```

Verify it works:

```bash
# Check FACT imports
docker run --rm fact-test python3 -c "import fact; print('OK')"

# Check libclang loads
docker run --rm fact-test python3 -c "from clang.cindex import Index; idx = Index.create(); print('OK')"

# Check GCC
docker run --rm fact-test gcc --version

# Check user
docker run --rm fact-test id
```

Run the included test exercise:

```bash
docker run --rm \
  -v $(pwd)/test-exercise/assignment:/home/assignment \
  -v $(pwd)/test-exercise/tests:/home/tests \
  fact-test \
  bash -c "cd /home/tests && python3 -c \"
from fact.tester import Tester
t = Tester.from_config('tests.yaml')
t.run()
t.export_result()
print('All tests passed!' if t.successful() else 'TESTS FAILED')
\""
```

## Multi-arch Build

```bash
docker buildx build --platform linux/amd64,linux/arm64 .
```

## Publishing

Push a tag to trigger the CI workflow which builds and pushes to DockerHub and GitHub Container Registry:

```bash
git tag v1.0.0
git push --tags
```
