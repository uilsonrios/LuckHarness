import os, sys, tempfile

# isola banco/prefs/log num diretório temporário antes de importar o core
os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
