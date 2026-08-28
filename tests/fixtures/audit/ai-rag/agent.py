import openai

SYSTEM_PROMPT = 'you are an agent'


def answer(q, vector_store):
    ctx = vector_store.search(q)
    return openai.chat.completions.create(model='gpt-4', messages=[{'role':'user','content':q}])
