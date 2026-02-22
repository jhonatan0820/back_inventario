#!/bin/bash

# Activar el endpoint para despertar la base de datos
echo "🔄 Despertando la base de datos..."
curl -X GET https://render.dotacioneszambrano.com/activador || true

# Esperar un momento
sleep 2

# Iniciar Gunicorn
echo "🚀 Iniciando servidor..."
gunicorn -c gunicorn.conf.py app:app
