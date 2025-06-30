import speech_recognition as sr
from .stt import STTHandler

class SphinxHandler(STTHandler):
    key = "sphinx"
    
    @staticmethod
    def get_extra_requirements() -> list:
        return ["pocketsphinx"]

    def recognize_file(self, path):
        r = sr.Recognizer()
        with sr.AudioFile(path) as source:
            audio = r.record(source)

        try:
            res = r.recognize_sphinx(audio)
        except sr.UnknownValueError:
            res = _("Could not understand the audio")
        except Exception as e:
            print(e)
            return None
        return res

