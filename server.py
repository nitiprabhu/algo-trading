from flask import Flask, request
import requests

app = Flask(__name__)

@app.route('/api/upstox/token_webhook/<secret>', methods=['POST'])
def forward(secret):
    r = requests.post(f'https://glacy.online/api/upstox/token_webhook/{secret}', json=request.json)
    return r.json(), r.status_code

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
