FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LIMS_ENV=production LIMS_HOST=0.0.0.0 PORT=8080 LIMS_DATA_DIR=/var/lib/lims TZ=Asia/Shanghai
VOLUME ["/var/lib/lims"]
EXPOSE 8080
CMD ["python", "app.py"]
