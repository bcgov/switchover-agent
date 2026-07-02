FROM python:3.11-alpine

WORKDIR /app

RUN apk add curl build-base python3-dev libffi-dev

RUN python -m pip install --upgrade pip

ENV XDG_CONFIG_HOME=/var

RUN cd /tmp && \
  curl -sSL https://install.python-poetry.org | POETRY_VERSION=2.2.1 POETRY_HOME=/opt/poetry python3 -

RUN ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry && \
  poetry config virtualenvs.create false && \
  chmod g+r /var/pypoetry/config.toml

COPY ./pyproject.toml /tmp/
COPY ./poetry.lock /tmp/

RUN cd /tmp && poetry install --no-root --only main

COPY ./src /app/src
COPY ./pyproject.toml /app

EXPOSE 8000

ENV PROMETHEUS_MULTIPROC_DIR=/tmp
ENV PY_ENV=local

ENTRYPOINT ["poetry", "run"]
CMD ["python", "src/main.py"]
