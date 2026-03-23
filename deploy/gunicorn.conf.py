# Gunicorn configuration for Order System

bind = '127.0.0.1:8100'
workers = 2
timeout = 120
accesslog = '/opt/order/logs/access.log'
errorlog = '/opt/order/logs/error.log'
loglevel = 'info'
