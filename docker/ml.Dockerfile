# ML experiment image (improvement plan A2): Python 3.12 with numpy, scipy, scikit-learn and CPU torch.
#   docker build -f docker/ml.Dockerfile -t quaera-ml docker
# For GPU experiments use the host environment (`quaera setup ml` / install.sh --with-ml) or a remote runner.
FROM python:3.12-slim

ARG TORCH=1
RUN python -m venv /opt/ml-env \
    && /opt/ml-env/bin/pip install --no-cache-dir numpy scipy scikit-learn \
    && if [ "$TORCH" = 1 ]; then /opt/ml-env/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; fi \
    && chmod -R a+rX /opt/ml-env \
    && printf '{"kind": "ml", "python": "/opt/ml-env/bin/python", "path": "/opt/ml-env/bin:/usr/local/bin:/usr/bin:/bin"}\n' > /etc/quaera-image.json
