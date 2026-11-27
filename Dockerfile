# Советник в контейнере: стенд, адрес для кабинета (POST /api/advice), журнал и отчёт пилота.
# В образе — код и открытые данные каталога из репозитория. Данных организаторов (data/private) и ключей
# модели (.env) в образе нет: совет клиенту модель не вызывает, агенту каталога ключ передают при запуске.
#   docker build -t sovetnik .
#   docker run -p 8765:8765 sovetnik                         стенд: http://127.0.0.1:8765
#   docker run -p 8765:8765 -e SOVETNIK_CONTROL_SHARE=0.5 sovetnik    пилот: половина клиентов — контроль
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY recsys recsys
COPY knowledge knowledge
COPY data/interim data/interim
COPY reports reports
COPY site site
EXPOSE 8765
CMD ["python", "-m", "recsys.server", "--host", "0.0.0.0", "--port", "8765"]
