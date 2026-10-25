"""Official SQuAD/Hotpot answer normalization, max over gold aliases; standard ROUGE."""
import re, string, collections
from rouge_score.rouge_scorer import RougeScorer


def normalize_answer(text):
    return ' '.join(
        re.sub(r'\b(a|an|the)\b', ' ', ''.join(c for c in text.lower() if c not in string.punctuation)).split())


def normalize_trivia_answer(text):
    # TriviaQA official evaluation: punctuation becomes whitespace, including curly quotes.
    exclude = set(string.punctuation + '‘’´`')
    return ' '.join(
        re.sub(r'\b(a|an|the)\b', ' ', ''.join(' ' if c in exclude else c for c in text.lower())).split()).strip()


def qa_score(prediction, golds, hotpot=False):
    results = []
    for gold in golds:
        normalize = normalize_answer if hotpot else normalize_trivia_answer
        p, g = normalize(prediction), normalize(gold)
        exact = float(p == g)
        if hotpot and p != g and (p in ['yes', 'no', 'noanswer'] or g in ['yes', 'no', 'noanswer']):
            f1 = 0.0
        else:
            pt, gt = p.split(), g.split();
            common = sum((collections.Counter(pt) & collections.Counter(gt)).values())
            if not pt or not gt:
                f1 = float(pt == gt)
            elif not common:
                f1 = 0.0
            else:
                precision, recall = common / len(pt), common / len(gt);
                f1 = 2 * precision * recall / (precision + recall)
        results.append({'em': exact, 'f1': f1})
    return {key: max(r[key] for r in results) for key in ['em', 'f1']}


def vote(outputs, task='hotpotqa'):
    normalize = normalize_answer if task == 'hotpotqa' else normalize_trivia_answer
    keys = [normalize(x) for x in outputs];
    winner = collections.Counter(keys).most_common(1)[0][0]
    return outputs[keys.index(winner)]


def score(record, text):
    if record['task'] == 'gov_report':
        scorer = RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=True)
        values = [scorer.score(g, text) for g in record['answers']]
        return {k: max(v[k].fmeasure for v in values) for k in ['rouge1', 'rouge2', 'rougeL']}
    if record['task'] == 'synthetic':
        cited = {int(x) for x in re.findall(r'\b(?:Record\s*)?0?([1-9]|1[0-9]|2[0-5])\b', text, re.I)}
        for start, end in re.findall(
                r'\bRecords?\s+0?([1-9]|1[0-9]|2[0-5])\s*(?:to|through|[-–])\s*0?([1-9]|1[0-9]|2[0-5])\b', text, re.I):
            if int(start) <= int(end) and int(end) - int(start) <= 24: cited.update(range(int(start), int(end) + 1))
        citation = float(cited == set(record['expected_records']))
        topic = float(any(t in text.lower() for t in record['topic_terms']))
        explanatory = float(len(re.findall(r'\b\w+\b', text)) >= 20)
        return {'citation': citation, 'topic': topic, 'joint': citation * topic * explanatory}
    return qa_score(text, record['answers'], hotpot=record['task'] == 'hotpotqa')
