const EPSILON = 1e-9;

export const DEFAULT_SAMPLE_COUNT = 28;

export function clamp(value, minimum = 0, maximum = 1) {
  return Math.min(maximum, Math.max(minimum, value));
}

export function toXY(point) {
  const x = Array.isArray(point) ? point[0] : point?.x;
  const y = Array.isArray(point) ? point[1] : point?.y;
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new TypeError("stroke points must contain finite x and y values");
  }
  return [Number(x), Number(y)];
}

export function sanitizeStroke(
  points,
  { maxPoints = 4096, minStep = 0.00025, boundsTolerance = 0.25 } = {},
) {
  if (!Array.isArray(points)) {
    throw new TypeError("stroke must be an array");
  }
  if (points.length > maxPoints) {
    throw new RangeError(`stroke exceeds the ${maxPoints} point limit`);
  }

  const clean = [];
  for (const point of points) {
    const [rawX, rawY] = toXY(point);
    if (
      rawX < -boundsTolerance || rawX > 1 + boundsTolerance
      || rawY < -boundsTolerance || rawY > 1 + boundsTolerance
    ) {
      throw new RangeError("stroke point is outside the supported canvas bounds");
    }
    const next = [clamp(rawX), clamp(rawY)];
    const previous = clean.at(-1);
    if (!previous || distance(previous, next) >= minStep) {
      clean.push(next);
    }
  }
  return clean;
}

export function distance(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

export function strokeArcLength(points) {
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    total += distance(points[index - 1], points[index]);
  }
  return total;
}

export function resampleStroke(points, count = DEFAULT_SAMPLE_COUNT) {
  if (!Number.isInteger(count) || count < 2) {
    throw new RangeError("resample count must be an integer greater than one");
  }
  const clean = sanitizeStroke(points);
  if (clean.length === 0) return [];
  if (clean.length === 1) {
    return Array.from({ length: count }, () => [...clean[0]]);
  }

  const cumulative = [0];
  for (let index = 1; index < clean.length; index += 1) {
    cumulative.push(cumulative.at(-1) + distance(clean[index - 1], clean[index]));
  }
  const total = cumulative.at(-1);
  if (total < EPSILON) {
    return Array.from({ length: count }, () => [...clean[0]]);
  }

  const output = [];
  let segment = 1;
  for (let sample = 0; sample < count; sample += 1) {
    const target = total * sample / (count - 1);
    while (segment < cumulative.length - 1 && cumulative[segment] < target) {
      segment += 1;
    }
    const startDistance = cumulative[segment - 1];
    const endDistance = cumulative[segment];
    const ratio = (target - startDistance) / Math.max(endDistance - startDistance, EPSILON);
    const start = clean[segment - 1];
    const end = clean[segment];
    output.push([
      start[0] + (end[0] - start[0]) * ratio,
      start[1] + (end[1] - start[1]) * ratio,
    ]);
  }
  return output;
}

export function bandedDtw(first, second, { bandRatio = 0.3 } = {}) {
  if (first.length === 0 || second.length === 0) {
    return { distance: Number.POSITIVE_INFINITY, path: [] };
  }
  const rows = first.length;
  const columns = second.length;
  const band = Math.max(Math.abs(rows - columns), Math.ceil(Math.max(rows, columns) * bandRatio));
  const costs = Array.from({ length: rows + 1 }, () => (
    new Float64Array(columns + 1).fill(Number.POSITIVE_INFINITY)
  ));
  const previous = Array.from({ length: rows + 1 }, () => new Uint8Array(columns + 1));
  costs[0][0] = 0;

  for (let row = 1; row <= rows; row += 1) {
    const start = Math.max(1, row - band);
    const end = Math.min(columns, row + band);
    for (let column = start; column <= end; column += 1) {
      const local = distance(first[row - 1], second[column - 1]);
      const diagonal = costs[row - 1][column - 1];
      const up = costs[row - 1][column];
      const left = costs[row][column - 1];
      if (diagonal <= up && diagonal <= left) {
        costs[row][column] = local + diagonal;
        previous[row][column] = 1;
      } else if (up <= left) {
        costs[row][column] = local + up;
        previous[row][column] = 2;
      } else {
        costs[row][column] = local + left;
        previous[row][column] = 3;
      }
    }
  }

  if (!Number.isFinite(costs[rows][columns])) {
    return { distance: Number.POSITIVE_INFINITY, path: [] };
  }
  const path = [];
  let row = rows;
  let column = columns;
  while (row > 0 && column > 0) {
    path.push([row - 1, column - 1]);
    const step = previous[row][column];
    if (step === 1) {
      row -= 1;
      column -= 1;
    } else if (step === 2) {
      row -= 1;
    } else if (step === 3) {
      column -= 1;
    } else {
      break;
    }
  }
  path.reverse();
  return { distance: costs[rows][columns] / Math.max(path.length, 1), path };
}

function meanPoint(points) {
  const sum = points.reduce(
    (current, point) => [current[0] + point[0], current[1] + point[1]],
    [0, 0],
  );
  return [sum[0] / points.length, sum[1] / points.length];
}

function boundingBox(points) {
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const [x, y] of points) {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  return {
    center: [(minX + maxX) / 2, (minY + maxY) / 2],
    diagonal: Math.hypot(maxX - minX, maxY - minY),
  };
}

function directionCosine(user, template) {
  const userVector = [user.at(-1)[0] - user[0][0], user.at(-1)[1] - user[0][1]];
  const templateVector = [
    template.at(-1)[0] - template[0][0],
    template.at(-1)[1] - template[0][1],
  ];
  const denominator = Math.hypot(...userVector) * Math.hypot(...templateVector);
  if (denominator < EPSILON) return 0;
  return clamp(
    (userVector[0] * templateVector[0] + userVector[1] * templateVector[1]) / denominator,
    -1,
    1,
  );
}

function curvature(points) {
  const values = new Float64Array(points.length);
  for (let index = 1; index < points.length - 1; index += 1) {
    const first = [
      points[index][0] - points[index - 1][0],
      points[index][1] - points[index - 1][1],
    ];
    const second = [
      points[index + 1][0] - points[index][0],
      points[index + 1][1] - points[index][1],
    ];
    const cross = first[0] * second[1] - first[1] * second[0];
    const dot = first[0] * second[0] + first[1] * second[1];
    values[index] = Math.atan2(cross, dot);
  }
  return values;
}

function alignedMeanDistance(first, second, path) {
  if (path.length === 0) return Number.POSITIVE_INFINITY;
  let total = 0;
  for (const [firstIndex, secondIndex] of path) {
    total += distance(first[firstIndex], second[secondIndex]);
  }
  return total / path.length;
}

export function computeStrokeMetrics(userPoints, templatePoints, options = {}) {
  const sampleCount = options.sampleCount ?? DEFAULT_SAMPLE_COUNT;
  const rawUser = sanitizeStroke(userPoints);
  const rawTemplate = sanitizeStroke(templatePoints);
  if (rawUser.length === 0 || rawTemplate.length === 0) {
    throw new RangeError("user and template strokes must each contain a point");
  }

  const user = resampleStroke(rawUser, sampleCount);
  const template = resampleStroke(rawTemplate, sampleCount);
  const forward = bandedDtw(user, template, options);
  const reversedUser = [...user].reverse();
  const reversed = bandedDtw(reversedUser, template, options);
  const userCenter = meanPoint(user);
  const templateCenter = meanPoint(template);
  const centeredUser = user.map(([x, y]) => [x - userCenter[0], y - userCenter[1]]);
  const centeredTemplate = template.map(([x, y]) => [x - templateCenter[0], y - templateCenter[1]]);
  const userBox = boundingBox(user);
  const templateBox = boundingBox(template);
  const normalizedUser = centeredUser.map(([x, y]) => [
    x / Math.max(userBox.diagonal, EPSILON),
    y / Math.max(userBox.diagonal, EPSILON),
  ]);
  const normalizedTemplate = centeredTemplate.map(([x, y]) => [
    x / Math.max(templateBox.diagonal, EPSILON),
    y / Math.max(templateBox.diagonal, EPSILON),
  ]);
  const formAlignment = bandedDtw(normalizedUser, normalizedTemplate, options);
  const fittedScale = userBox.diagonal >= EPSILON && templateBox.diagonal >= EPSILON
    ? userBox.diagonal / templateBox.diagonal
    : 1;
  const fittedTemplate = centeredTemplate.map(([x, y]) => [
    userBox.center[0] + x * fittedScale,
    userBox.center[1] + y * fittedScale,
  ]);
  const userCurvature = curvature(user);
  const templateCurvature = curvature(template);

  let hotspot = { difference: 0, userIndex: 0, templateIndex: 0 };
  let maximumAlignedError = { distance: 0, pathIndex: 0 };
  let maximumFormError = { distance: 0, pathIndex: 0 };
  forward.path.forEach(([userIndex, templateIndex], pathIndex) => {
    const difference = Math.abs(userCurvature[userIndex] - templateCurvature[templateIndex]);
    if (difference > hotspot.difference) {
      hotspot = { difference, userIndex, templateIndex };
    }
    const alignedError = distance(user[userIndex], template[templateIndex]);
    if (alignedError > maximumAlignedError.distance) {
      maximumAlignedError = { distance: alignedError, pathIndex };
    }
  });
  formAlignment.path.forEach(([userIndex, templateIndex], pathIndex) => {
    const alignedError = distance(
      normalizedUser[userIndex],
      normalizedTemplate[templateIndex],
    );
    if (alignedError > maximumFormError.distance) {
      maximumFormError = { distance: alignedError, pathIndex };
    }
  });

  const segmentStart = Math.max(0, maximumAlignedError.pathIndex - 2);
  const segmentEnd = Math.min(forward.path.length, maximumAlignedError.pathIndex + 3);
  const problemPath = forward.path.slice(segmentStart, segmentEnd);
  const formSegmentStart = Math.max(0, maximumFormError.pathIndex - 2);
  const formSegmentEnd = Math.min(
    formAlignment.path.length,
    maximumFormError.pathIndex + 3,
  );
  const formProblemPath = formAlignment.path.slice(formSegmentStart, formSegmentEnd);
  const direction = directionCosine(user, template);
  const pathError = alignedMeanDistance(user, template, forward.path);
  const reversePathError = reversed.distance;

  return {
    startError: distance(user[0], template[0]),
    endError: distance(user.at(-1), template.at(-1)),
    pathError,
    shapeError: alignedMeanDistance(centeredUser, centeredTemplate, forward.path),
    formError: formAlignment.distance,
    directionCosine: direction,
    // Fixed-density paths keep high-rate pen jitter from becoming fake length.
    lengthRatio: strokeArcLength(user) / Math.max(strokeArcLength(template), EPSILON),
    bboxShift: {
      dx: templateBox.center[0] - userBox.center[0],
      dy: templateBox.center[1] - userBox.center[1],
      magnitude: distance(userBox.center, templateBox.center),
    },
    scaleRatio: userBox.diagonal / Math.max(templateBox.diagonal, EPSILON),
    curvatureHotspot: {
      difference: hotspot.difference,
      user: user[hotspot.userIndex],
      target: template[hotspot.templateIndex],
    },
    reversePathError,
    reverseAdvantage: clamp((pathError - reversePathError) / Math.max(pathError, 0.02), -1, 1),
    looksReversed: direction < -0.35 && reversePathError + 0.012 < pathError,
    startVector: {
      dx: template[0][0] - user[0][0],
      dy: template[0][1] - user[0][1],
    },
    endVector: {
      dx: template.at(-1)[0] - user.at(-1)[0],
      dy: template.at(-1)[1] - user.at(-1)[1],
    },
    problemSegment: problemPath.map(([userIndex]) => user[userIndex]),
    targetSegment: problemPath.map(([, templateIndex]) => template[templateIndex]),
    formProblemSegment: formProblemPath.map(([userIndex]) => user[userIndex]),
    formTargetSegment: formProblemPath.map(
      ([, templateIndex]) => fittedTemplate[templateIndex],
    ),
    alignedUser: user,
    alignedTemplate: template,
  };
}

export function computePartialMetrics(userPoints, templatePoints) {
  const rawUser = sanitizeStroke(userPoints);
  const rawTemplate = sanitizeStroke(templatePoints);
  if (rawUser.length < 2 || strokeArcLength(rawUser) < 0.008) return null;

  const templateDense = resampleStroke(rawTemplate, 64);
  const progress = clamp(
    strokeArcLength(rawUser) / Math.max(strokeArcLength(rawTemplate), EPSILON),
    0.08,
    1,
  );
  const prefixLength = Math.max(4, Math.min(64, Math.round(progress * 63) + 1));
  const templatePrefix = templateDense.slice(0, prefixLength);
  const count = Math.max(8, Math.min(24, rawUser.length));
  const user = resampleStroke(rawUser, count);
  const template = resampleStroke(templatePrefix, count);
  const aligned = bandedDtw(user, template, { bandRatio: 0.25 });
  const pathError = alignedMeanDistance(user, template, aligned.path);
  return {
    pathError,
    confidence: clamp((pathError - 0.05) / 0.08),
    problemSegment: user.slice(-Math.min(5, user.length)),
    targetSegment: template.slice(-Math.min(5, template.length)),
  };
}

export function toLegacyStroke(points) {
  return points.map((point) => toXY(point));
}

export function toLegacyStrokes(strokes) {
  return strokes.map((stroke) => toLegacyStroke(stroke));
}
