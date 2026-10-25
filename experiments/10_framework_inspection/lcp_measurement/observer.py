"""Common offline G observer. No framework imports, name branches, or prompt edits."""
from pathlib import Path
import ast, hashlib, json, re
from frozen_core import locate as old_locate

ENCODINGS = ("identity", "json_string_utf8")


def digest_bytes(b): return hashlib.sha256(b).hexdigest()


def encode_text(s, kind):
    s.encode("utf8", errors="strict")
    return s if kind == "identity" else json.dumps(s, ensure_ascii=False)[1:-1]


def occurrences(haystack, needle):
    if not needle: return []
    out = [];
    start = 0
    while True:
        at = haystack.find(needle, start)
        if at < 0: return out
        out.append((at, at + len(needle)));
        start = at + 1


def physical_hits(rendered, needle):
    raw = rendered.encode("utf8", errors="strict")
    boundaries = {0: 0};
    n = 0
    for i, c in enumerate(rendered): n += len(c.encode("utf8"));boundaries[n] = i + 1
    merged = {}
    for kind in ENCODINGS:
        value = encode_text(needle, kind).encode("utf8")
        for a, b in occurrences(raw, value):
            if a not in boundaries or b not in boundaries: continue
            span = (boundaries[a], boundaries[b])
            merged.setdefault(span, []).append(kind)
    return [{"chars": [a, b], "encodings": labels} for (a, b), labels in sorted(merged.items())]


def token_span(chars, offsets):
    a, b = chars
    ids = [i for i, (x, y) in enumerate(offsets) if y > x and y > a and x < b]
    return [min(ids), max(ids) + 1] if ids else None


def valid_offsets(rendered, ids, offsets):
    return len(ids) == len(offsets) and all(
        isinstance(a, int) and isinstance(b, int) and 0 <= a <= b <= len(rendered) for a, b in offsets)


def ast_hash(node): return digest_bytes(ast.dump(node, include_attributes=False).encode("utf8"))


def _functions(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _select_expr(tree, spec):
    funcs = _functions(tree, spec["function"])
    if len(funcs) != 1: raise ValueError("source_function_not_unique")
    fn = funcs[0]
    if spec.get("assignment"):
        nodes = [n.value for n in ast.walk(fn) if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == spec["assignment"] for t in n.targets)]
    elif spec.get("message_symbol"):
        name = spec["message_symbol"];
        nodes = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "content":
                        nodes.extend(n for n in ast.walk(value) if isinstance(n, ast.Name) and n.id == name)
        # All identical symbol references designate the same native scalar,
        # with the fixed source tying g to ctx.retrieve and raw_chunks.
        if not nodes: raise ValueError("native_symbol_not_in_message_content")
        if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "retrieve" for n in
                   ast.walk(fn)):
            raise ValueError("native_retrieval_origin_absent")
        nodes = nodes[:1]
    else:
        nodes = [n.value for n in fn.body if isinstance(n, ast.Return)]
    if len(nodes) != 1: raise ValueError("assembly_expression_not_unique")
    return nodes[0]


class SourceStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.policy = json.loads((self.root / "config/policy.json").read_text(encoding="utf8"))
        path = self.root / "config/source_registry.json"
        raw = path.read_bytes()
        if digest_bytes(raw) != self.policy["source_registry_sha256"]: raise ValueError("registry_hash_mismatch")
        self.registry = json.loads(raw)["contracts"]

    def plan(self, contract_id, count):
        if contract_id not in self.registry: raise ValueError("unregistered_source_contract")
        spec = self.registry[contract_id]
        if spec["scope"] not in ("synthetic", "constructed_control"):
            raise ValueError("real_source_contract_gate_closed")
        p = (self.root / spec["source_file"]).resolve()
        if not p.is_relative_to(self.root): raise ValueError("source_path_outside_package")
        raw = p.read_bytes()
        if digest_bytes(raw) != spec["source_sha256"]: raise ValueError("source_sha_mismatch")
        text = raw.decode("utf8");
        node = _select_expr(ast.parse(text), spec["selector"])
        if ast_hash(node) != spec["expression_ast_sha256"]: raise ValueError("source_expression_AST_mismatch")
        codec = "identity"
        # Only one exact JSON string-content conversion is admissible.
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            call = node.value;
            sl = node.slice
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and isinstance(call.func.value,
                                                                                                       ast.Name) and call.func.value.id == "json" and call.func.attr == "dumps" and len(
                    call.args) == 1 and len(call.keywords) == 1 and call.keywords[
                        0].arg == "ensure_ascii" and isinstance(call.keywords[0].value, ast.Constant) and call.keywords[
                        0].value.value is False and isinstance(sl.lower,
                                                               ast.Constant) and sl.lower.value == 1 and isinstance(
                    sl.upper, ast.UnaryOp) and isinstance(sl.upper.op, ast.USub) and isinstance(sl.upper.operand,
                                                                                                ast.Constant) and sl.upper.operand.value == 1 and sl.step is None):
                raise ValueError("JSON_configuration_not_frozen")
            codec = "json_string_utf8";
            node = call.args[0]
        symbol = spec["native_symbol"]

        def sequence(arg):
            if isinstance(arg, ast.Name) and arg.id == symbol: return list(range(count))
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id == "reversed" and len(
                arg.args) == 1 and not arg.keywords and isinstance(arg.args[0], ast.Name) and arg.args[
                0].id == symbol: return list(reversed(range(count)))
            if isinstance(arg, ast.GeneratorExp) and len(arg.generators) == 1:
                g = arg.generators[0]
                if not g.ifs and not g.is_async and isinstance(g.target, ast.Name) and isinstance(g.iter,
                                                                                                  ast.Name) and g.iter.id == symbol and isinstance(
                        arg.elt, ast.Attribute) and isinstance(arg.elt.value,
                                                               ast.Name) and arg.elt.value.id == g.target.id and arg.elt.attr == spec.get(
                        "native_accessor"):
                    return list(range(count))
            raise ValueError("unsupported_source_sequence")

        def walk(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, str): return [("literal", n.value)]
            if isinstance(n, ast.Name) and n.id == symbol and spec.get("scalar"):
                if count != 1: raise ValueError("scalar_source_native_arity")
                return [("chunk", 0)]
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and n.value.id == symbol and isinstance(
                n.slice, ast.Constant) and type(n.slice.value) is int: return [("chunk", n.slice.value)]
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add): return walk(n.left) + walk(n.right)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join" and isinstance(
                    n.func.value, ast.Constant) and isinstance(n.func.value.value, str) and len(
                    n.args) == 1 and not n.keywords:
                refs = sequence(n.args[0]);
                out = []
                for i, idx in enumerate(refs):
                    if i: out.append(("literal", n.func.value.value))
                    out.append(("chunk", idx))
                return out
            raise ValueError("unsupported_assembly_expression")

        fragments = walk(node);
        refs = [x for k, x in fragments if k == "chunk"]
        if sorted(refs) != list(range(count)): raise ValueError("source_omits_repeats_or_invents_native_chunks")
        return {"fragments": fragments, "codec": codec, "source_file": spec["source_file"],
                "source_sha256": spec["source_sha256"], "expression_ast_sha256": spec["expression_ast_sha256"],
                "expression_text": ast.get_source_segment(text, _select_expr(ast.parse(text), spec["selector"])),
                "native_field": spec["native_field"], "scope": spec["scope"]}


def message_fields(messages):
    fields = []

    def visit(value, path):
        if isinstance(value, str):
            fields.append((path, value))
        elif isinstance(value, dict):
            for key, v in value.items(): visit(v, path + [key])
        elif isinstance(value, list):
            for i, v in enumerate(value): visit(v, path + [i])

    for i, msg in enumerate(messages): visit(msg.get("content"), ["messages", i, "content"])
    return fields


class Observer:
    def __init__(self, root):
        self.store = SourceStore(root);
        self.calls = 0

    def observe(self, item, native, binding, contract_id, history, queries, phase):
        self.calls += 1
        rendered = item["rendered"];
        offsets = item["offsets"];
        ids = item["token_ids"]
        result = {"G": {"status": "ambiguous"}, "S": {"status": "ambiguous"}, "proof": {}}

        def reject(reason, diagnostic=None):
            result["G"] = {"status": "ambiguous", "reason": reason}
            if diagnostic is not None: result["G"]["diagnostic_envelope"] = diagnostic
            return result

        try:
            if not valid_offsets(rendered, ids, offsets): return reject("invalid_saved_Unicode_token_offsets")
            if not isinstance(history, str) or not history: return reject("complete_S_missing")
            result["S"] = old_locate(rendered, history, offsets)
            policy = self.store.policy
            for key in ("sentinel_S", "sentinel_S_end"):
                sentinel = policy[key]
                if len(occurrences(history, sentinel)) != 1 or len(occurrences(rendered, sentinel)) != 1:
                    return reject("S_sentinel_not_unique")
            if result["S"]["status"] != "ok": return reject("complete_S_not_unique")
            chunks = native.get("raw_chunks");
            returned = native.get("returned_chunks")
            if not isinstance(chunks, list) or not chunks or not all(
                isinstance(c, str) and c for c in chunks): return reject("empty_or_unrecoverable_native_chunk")
            if len(set(chunks)) != len(chunks): return reject("duplicate_native_chunk")
            for c in chunks: c.encode("utf8", errors="strict")
            if not isinstance(returned, list) or len(returned) != len(chunks): return reject(
                "returned_chunks_unrecoverable")
            key = native.get("call_key")
            if not isinstance(key, str) or not re.fullmatch(r"R(?:\d+|broadcast)_C\d{4,}", key): return reject(
                "native_call_key_invalid")
            marker = policy["marker_format"].format(call_key=key)
            expected = list(chunks)
            if phase == "attacked": expected[0] = marker + expected[0]
            if returned != expected: return reject("returned_chunks_modified_beyond_frozen_marker")
            record = item["request"]
            if binding.get("call_key") != key or binding.get("source_replica") != native.get("replica") or binding.get(
                    "target_replica") != record["replica"] or binding.get("request_round") != item["request_round"]:
                return reject("native_call_request_round_binding_mismatch")
            if json.loads(record["raw_body_text"]) != record["body"]: return reject("raw_request_body_payload_mismatch")
            locations = []
            for i, c in enumerate(chunks):
                hits = physical_hits(rendered, c)
                if len(hits) != 1: return reject("chunk_missing_or_joint_encoding_ambiguous")
                locations.append({**hits[0], "native_index": i})
            ordered = sorted(locations, key=lambda x: x["chars"][0])
            if any(a["chars"][1] > b["chars"][0] for a, b in zip(ordered, ordered[1:])): return reject(
                "chunk_character_overlap_or_nesting")
            envelope = [ordered[0]["chars"][0], ordered[-1]["chars"][1]]
            gaps = [{"chars": [a["chars"][1], b["chars"][0]], "text": rendered[a["chars"][1]:b["chars"][0]]} for a, b in
                    zip(ordered, ordered[1:])]
            for g in gaps: g.update({"Unicode_codepoints": len(g["text"]), "UTF8_bytes": len(g["text"].encode("utf8"))})
            diagnostic = {"chars": envelope, "tokens": token_span(envelope, offsets), "gaps": gaps,
                          "rendered_native_order": [x["native_index"] for x in ordered]}
            try:
                plan = self.store.plan(contract_id, len(chunks))
            except ValueError as e:
                return reject(str(e), diagnostic)
            # Reconstruct ONLY from the verified source AST and archived returned fields.
            parts = [];
            relative = {};
            length = 0
            for kind, value in plan["fragments"]:
                text = returned[value] if kind == "chunk" else value
                part = encode_text(text, plan["codec"]);
                parts.append(part)
                if kind == "chunk":
                    prefix = len(encode_text(marker, plan["codec"])) if phase == "attacked" and value == 0 else 0
                    relative[value] = [length + prefix, length + len(part)]
                length += len(part)
            block = "".join(parts)
            candidates = []
            for path, field in message_fields(record["body"]["messages"]):
                spans = occurrences(field, block)
                if len(spans) == 1: candidates.append((path, field, spans[0]))
            if len(candidates) != 1: return reject("source_block_not_unique_in_one_model_message_field", diagnostic)
            path, field, block_span = candidates[0]
            field_hits = physical_hits(rendered, field)
            if len(field_hits) != 1: return reject("model_field_render_mapping_not_jointly_unique", diagnostic)
            mapping = field_hits[0];
            mapped = []
            for render_codec in mapping["encodings"]:
                if plan["codec"] == "json_string_utf8" and render_codec == "json_string_utf8":
                    # For ASCII equivalence, identity mapping is enough; never try a second layer.
                    continue
                effective = "json_string_utf8" if "json_string_utf8" in (plan["codec"], render_codec) else "identity"
                predicted = []
                for i, span in relative.items():
                    a = block_span[0] + span[0];
                    b = block_span[0] + span[1]
                    if render_codec == "identity":
                        pred = [mapping["chars"][0] + a, mapping["chars"][0] + b]
                    else:
                        pred = [mapping["chars"][0] + len(encode_text(field[:a], render_codec)),
                                mapping["chars"][0] + len(encode_text(field[:b], render_codec))]
                    observed = locations[i]
                    if pred != observed["chars"] or effective not in observed["encodings"]: break
                    predicted.append((i, pred))
                else:
                    mapped.append({"render_codec": render_codec, "effective_chunk_codec": effective,
                                   "predicted_chunks": predicted})
            if not mapped: return reject("source_generated_bytes_do_not_match_actual_rendered_field", diagnostic)
            if phase == "attacked" and ordered[0]["native_index"] != 0: return reject("marker_internal_after_reorder",
                                                                                      diagnostic)
            forbidden = [history, policy["sentinel_S"], policy["sentinel_S_end"]] + [q for q in queries if
                                                                                     isinstance(q, str) and q]
            for g in gaps:
                for value in forbidden:
                    if any(encode_text(value, e) in g["text"] for e in ENCODINGS): return reject(
                        "gap_contains_S_or_full_query", diagnostic)
                if re.search(policy["marker_regex"], g["text"]): return reject("gap_contains_marker", diagnostic)
            if any(g["UTF8_bytes"] > policy["glue_max_per_gap"] for g in gaps) or sum(g["UTF8_bytes"] for g in gaps) > \
                    policy["glue_max_total"]:
                return reject("source_proved_glue_exceeds_frozen_UTF8_limit", diagnostic)
            s = result["S"]["chars"]
            if envelope[0] < s[1] and s[0] < envelope[1]: return reject("G_overlaps_complete_S", diagnostic)
            gt = token_span(envelope, offsets);
            st = result["S"]["tokens"]
            if gt is None: return reject("no_G_token_overlap", diagnostic)
            if gt[0] < st[1] and st[0] < gt[1]: return reject("G_S_token_overlap", diagnostic)
            result["G"] = {"status": "ok", "chars": envelope, "tokens": gt, "native_chunk_locations": locations,
                           "reordered": [x["native_index"] for x in ordered] != list(range(len(chunks))), "gaps": gaps}
            result["proof"] = {"source_file": plan["source_file"], "source_sha256": plan["source_sha256"],
                               "assembly_expression_AST_sha256": plan["expression_ast_sha256"],
                               "assembly_expression": plan["expression_text"], "native_field": plan["native_field"],
                               "call_key": key, "source_replica": native["replica"],
                               "target_replica": record["replica"], "request_round": item["request_round"],
                               "actual_model_message_field": path,
                               "model_field_sha256": digest_bytes(field.encode("utf8")),
                               "source_generated_block_sha256": digest_bytes(block.encode("utf8")),
                               "rendered_sha256": digest_bytes(rendered.encode("utf8")), "render_mappings": mapped}
            return result
        except (UnicodeError, TypeError, KeyError, ValueError, IndexError) as e:
            return reject("input_or_source_integrity_error:" + type(e).__name__ + ":" + str(e))
