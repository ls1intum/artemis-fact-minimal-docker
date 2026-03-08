from setuptools import setup, find_packages

setup(
    name='fact',
    version='0.0.5',
    packages=find_packages(),
    package_data={'fact': ['schemas/*.json']},
    description='Test framework for C-code',
    install_requires=[
        'clang==14.0',
        'textx',
        'ruamel.yaml',
        'networkx',
        'pytest',
        'pytest-cov',
        'jsonschema',
        'numpy',
        'pandas',
        'openpyxl',
    ],
    python_requires='>=3.7',
)
