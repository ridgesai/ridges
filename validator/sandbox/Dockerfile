# NOTE: This Dockerfile is used to build the Docker image that will be used to run the sandbox.
#       To build the image, run:
#       docker build -t sandbox-runner .

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git patch && rm -rf /var/lib/apt/lists/*

RUN pip install numpy
RUN pip install pandas
RUN pip install scikit-learn
RUN pip install scipy
RUN pip install matplotlib
RUN pip install seaborn
RUN pip install xgboost
RUN pip install lightgbm
RUN pip install catboost
RUN pip install tensorflow
RUN pip install keras
RUN pip install torch
RUN pip install torchvision
RUN pip install torchaudio
RUN pip install transformers
RUN pip install sentence-transformers
RUN pip install datasets
RUN pip install statsmodels
RUN pip install nltk
RUN pip install spacy
RUN pip install gensim
RUN pip install opencv-python
RUN pip install mlflow
RUN pip install optuna
RUN pip install tqdm
RUN pip install joblib
RUN pip install dill
RUN pip install pickle-mixin
RUN pip install requests
RUN pip install urllib3

CMD ["python", "/sandbox/agent_runner.py"]