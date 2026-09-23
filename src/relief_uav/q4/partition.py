"""Canonical restricted-growth enumeration of non-equivalent set partitions."""

from collections.abc import Iterator


def enumerate_partitions(components: tuple, k: int) -> Iterator[tuple[tuple[str, ...], ...]]:
    if k < 1 or k > len(components):
        return
    labels = [0] * len(components)

    def visit(index: int, used: int):
        if used + len(components) - index < k:
            return
        if index == len(components):
            if used == k:
                groups = [[] for _ in range(k)]
                for atom, label in zip(components, labels):
                    groups[label].extend(atom.service_areas)
                yield tuple(tuple(sorted(g)) for g in groups)
            return
        for label in range(min(used + 1, k)):
            labels[index] = label
            yield from visit(index + 1, max(used, label + 1))

    yield from visit(1, 1)


def canonical_signature(groups: tuple[tuple[str, ...], ...]) -> str:
    return "|".join(",".join(group) for group in groups)
