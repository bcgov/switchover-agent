FROM python:3.7.10-alpine

WORKDIR /app

RUN apk add curl

RUN python -m pip install --upgrade pip

ENV XDG_CONFIG_HOME=/var

RUN cd /tmp && \
  curl -sSL https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py > get-poetry.py && \
  POETRY_HOME=/opt/poetry python get-poetry.py --version 1.0.8 && \
  cd /usr/local/bin && \
  chmod +x /opt/poetry/bin/poetry && \
  ln -s /opt/poetry/bin/poetry && \
  poetry config virtualenvs.create false && \
  chmod g+r /var/pypoetry/config.toml

COPY ./pyproject.toml /tmp/

COPY ./poetry.lock /tmp/

RUN cd /tmp && poetry install --no-root --no-dev

COPY ./src /app

EXPOSE 8000

ENTRYPOINT ["poetry", "run"]
CMD ["python", "main.py"]
