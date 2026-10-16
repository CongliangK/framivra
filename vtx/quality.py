"""Additive quality labels, not acceptance gates or authenticity proofs.

The definition and review status live in docs/cache-provenance.md. Evidence is
scoped: a gfx comparison must never upgrade the final movie's receipt.
"""

SCHEMA = 'vtx.quality/1'
STATUSES = {'passed', 'failed', 'partial', 'unknown'}
FIELDS = ('production_gates', 'visual_content', 'content_identity',
          'environment_identity', 'repeat_comparison', 'strict_exact')


def classify(*, scope, preview=False, evidence=None):
    """Deterministic display over independent purpose and evidence axes.

    This consumes normalized producer observations, not arbitrary external proof.
    Unrecognized/missing status values fail closed to unknown. Exact is scoped to
    #62's decoded RGBA comparison; it is not a claim about compressed movie bytes.
    """
    evidence = evidence if isinstance(evidence, dict) else {}
    states = {k: evidence.get(k) if isinstance(evidence.get(k), str)
              and evidence[k] in STATUSES else 'unknown' for k in FIELDS}
    production = all(states[k] == 'passed' for k in ('production_gates', 'visual_content'))
    reproducible = all(states[k] == 'passed' for k in
                       ('content_identity', 'environment_identity', 'repeat_comparison'))
    exact = all(states[k] == 'passed' for k in
                ('content_identity', 'environment_identity', 'strict_exact'))
    purpose = 'Preview' if preview is True else 'Production' if production else 'unknown'
    display = ('Verified Exact' if exact else 'Reproducible' if reproducible
               else purpose)
    return {'schema': SCHEMA, 'scope': scope, 'display': display,
            'purpose': purpose, 'reproducible': reproducible,
            'verified_exact': exact, 'evidence': states,
            'annotation_only': True}


def receipt_quality(record):
    """Runtime Fast identity and stage success do not establish visual acceptance."""
    stages = record.get('stages', {})
    stages = stages if isinstance(stages, dict) else {}
    # Old/current-fingerprint receipts can contain malformed observations.
    # Annotation must neither repair those records nor break an unrelated write.
    def observation(name):
        value = stages.get(name)
        return value if isinstance(value, dict) else {}
    qc = observation('qc')
    status = 'passed' if qc.get('ok') is True else 'failed' if qc.get('ok') is False else 'unknown'
    if status == 'passed' and qc.get('skipped_probes'):
        status = 'partial'
    preview = any(observation(k).get('ok') is True for k in ('video_draft', 'mux_draft'))
    # A formal mux/QC takes display precedence over an unrelated retained draft.
    preview = preview and not any(k in stages for k in ('mux', 'qc'))
    return classify(scope='final_movie', preview=preview,
                    evidence={'production_gates': status, 'content_identity': 'partial'})


def qc_quality(report):
    checks = report.get('checks')
    status = 'unknown'
    if isinstance(checks, list) and checks:
        if any(isinstance(x, dict) and x.get('pass') is False for x in checks):
            status = 'failed'
        elif all(isinstance(x, dict) and x.get('pass') is True for x in checks):
            status = 'passed'
    if status == 'passed' and report.get('skipped_probes'):
        status = 'partial'
    return classify(scope='final_movie', evidence={
        'production_gates': status, 'content_identity': 'partial'})


def golden_quality(report, *, fixed_environment=False):
    """Called by #62 verifier after validation, never by legacy comparison paths."""
    passed = report.get('ok') is True
    status = 'passed' if passed else 'partial' if report.get('compared_frames', 0) else 'unknown'
    return classify(scope='declared_gfx_instances:decoded_RGBA8', evidence={
        'content_identity': 'passed' if passed else 'unknown',
        'environment_identity': 'passed' if passed else 'unknown',
        'repeat_comparison': 'passed' if passed and fixed_environment else status if not passed else 'unknown',
        'strict_exact': 'passed' if passed else 'failed' if report.get('different_frames') else status})
