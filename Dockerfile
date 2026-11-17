FROM python:3.12-slim
WORKDIR /lab
COPY pyproject.toml requirements-lock.txt ./
COPY app app
COPY detector detector
COPY scenarios scenarios
COPY ground_truth ground_truth
COPY pipeline pipeline
COPY models models
COPY evaluation evaluation
COPY configs configs
RUN pip install --no-cache-dir -r requirements-lock.txt && pip install --no-cache-dir --no-deps .
ENV MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["python", "-m", "pipeline.cli"]
