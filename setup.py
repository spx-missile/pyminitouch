from setuptools import setup, find_packages


setup(
    name="pyminitouch",
    version="0.3.5",
    description="python wrapper of minitouch, for better experience",
    author="4pii4",
    author_email="pie@boobies.cc",
    url="https://github.com/4pii4/pyminitouch",
    packages=find_packages(),
    install_requires=["requests"],
)
