FROM python:3.9-alpine

WORKDIR /app

RUN apk add curl build-base python3-dev libffi-dev

RUN python -m pip install --upgrade pip

ENV XDG_CONFIG_HOME=/var

RUN cd /tmp && \
  curl -sSL https://install.python-poetry.org | POETRY_HOME=/opt/poetry python3 

RUN ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry && \
  poetry config virtualenvs.create false

COPY ./pyproject.toml /tmp/

COPY ./poetry.lock /tmp/

RUN cd /tmp && poetry install --no-root --no-dev

COPY ./src /app/src
COPY ./pyproject.toml /app

EXPOSE 8000

ENV PROMETHEUS_MULTIPROC_DIR=/tmp
ENV PY_ENV=local

ENTRYPOINT ["poetry", "run"]
CMD ["python", "src/main.py"]
