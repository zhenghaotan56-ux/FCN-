FROM ghcr.io/astral-sh/uv:0.9.24 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /bin/uv
WORKDIR /srv/fcn
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
ARG FCN_OPENBB_EXTRA=0
RUN if [ "$FCN_OPENBB_EXTRA" = 1 ]; then uv sync --locked --no-dev --extra serve --extra openbb; \
    else uv sync --locked --no-dev --extra serve; fi
COPY backend backend
COPY frontend frontend
COPY demo demo
COPY scripts scripts
COPY LICENSE ./
RUN useradd --create-home --uid 10001 fcn && mkdir -p var && chown fcn:fcn var
USER fcn
EXPOSE 8000
CMD ["/srv/fcn/.venv/bin/gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "60", "backend.app.main:create_app()"]
