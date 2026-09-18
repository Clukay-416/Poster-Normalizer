# Third-party components

This distribution preserves third-party packages as original wheel archives and official installers. Each component retains its own license; no blanket license is granted over all dependencies or models.

- CPython embedded Windows distribution: https://www.python.org/ — license is included inside the Python archive.
- Microsoft Visual C++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe — Microsoft installer terms apply.
- Python packages: package identities and exact versions are recorded in `offline/requirements-win313.lock`; license files and metadata remain inside the original `.whl` archives. Source index: https://pypi.org/.
- ONNX Runtime DirectML: https://github.com/microsoft/onnxruntime — see the bundled wheel's notices and license files.
- Model weights are not bundled. Model sources and license notes appear in the model center and `app/config/model_catalog.json`. Some models include independently licensed base models or assets; downloading does not grant additional rights.
- Optional online media sources (TMDB, TVmaze, Fanart) have their own service terms and image rights. Their results are candidates requiring review, not an assertion of ownership or commercial clearance.

The offline file manifest records artifact SHA256 hashes and official binary source URLs for provenance and integrity checking.

V0.4 adds isolated PyTorch runtime packs. Each pack preserves installed package metadata/licenses and an exact `requirements.lock` plus per-file SHA256 manifest. CUDA redistributables included by official PyTorch wheels retain NVIDIA/PyTorch terms.

Pinned inference code is assembled at build time from Meta SAM2 (Apache-2.0), OpenMMLab PowerPaint (MIT), and Alibaba AnyText2 (see retained upstream LICENSE). Revisions are in `app/config/ai_sources.json`; original notices remain under `app/ai_vendor`. The AnyText2 integration replaces only the ModelScope wrapper and disables its optional translator; the change is documented alongside the code.

The bundled Noto Sans SC font uses SIL Open Font License 1.1, included beside the font. Its immutable source URL and SHA256 are in `app/config/font_source.json`. Upstream example/system font collections are not redistributed.
