"""Pure, fixed retrieval-assembly expressions for constructed input truth."""
import json


def empty_join(chunks): return "".join(chunks)


def newline_join(chunks): return "\n\n".join(chunks)


def labelled_join(chunks): return chunks[0] + "\n\nSource 2: " + chunks[1] + "\n\nSource 3: " + chunks[2]


def json_empty(chunks): return json.dumps("".join(chunks), ensure_ascii=False)[1:-1]


def json_newline(chunks): return json.dumps("\n\n".join(chunks), ensure_ascii=False)[1:-1]


def json_labelled(chunks): return json.dumps(chunks[0] + "\n\nSource 2: " + chunks[1] + "\n\nSource 3: " + chunks[2],
                                             ensure_ascii=False)[1:-1]


def reversed_join(chunks): return "".join(reversed(chunks))


def gap64(chunks): return "                                                                ".join(chunks)


def gap65(chunks): return "                                                                 ".join(chunks)


def gap43(chunks): return "                                           ".join(chunks)


def gap_unicode33(chunks): return 'ééééééééééééééééééééééééééééééééé'.join(chunks)


def query_gap(chunks): return "What is the laboratory retention policy?".join(chunks)


def marker_gap(chunks): return "[SCAN_CALL_R9_C9999] ".join(chunks)


def legal_glue_LCP(chunks): return chunks[0] + "\n\nSource 2: alpha beta gamma delta epsilon zeta eta theta iota\n" + \
    chunks[1] + "\n\nSource 3: " + chunks[2]


def policy_word_gap(chunks): return "\n\nSource 2: policy\n".join(chunks)
