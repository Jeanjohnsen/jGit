from setuptools import setup

setup (
    name='jgit',
    version='2.0',
    packages=['jgit'],
    python_requires='>=3.9',
    entry_points= {
        'console_scripts' : [
            'jgit = jgit.cli:main'
        ]
    }
)