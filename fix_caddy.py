import re

with open("/Users/joebains/shopify-ai-blog-system/ai-blog-generator-python-server/service.sh", "r") as f:
    content = f.read()

bad_block = """    reverse_proxy /publar/rss-landingpages localhost:${PYTHON_BACKEND_PORT} {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-Proto https
        rewrite * /api/landing-pages/rss
    }"""

good_block = """    handle /publar/rss-landingpages {
        rewrite * /api/landing-pages/rss
        reverse_proxy localhost:${PYTHON_BACKEND_PORT} {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-Proto https
        }
    }"""

content = content.replace(bad_block, good_block)

with open("/Users/joebains/shopify-ai-blog-system/ai-blog-generator-python-server/service.sh", "w") as f:
    f.write(content)
