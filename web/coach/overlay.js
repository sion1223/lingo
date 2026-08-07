import { toXY } from "./metrics.js";

function drawPath(context, points, size) {
  if (!points || points.length < 2) return;
  const [startX, startY] = toXY(points[0]);
  context.beginPath();
  context.moveTo(startX * size, startY * size);
  for (const point of points.slice(1)) {
    const [x, y] = toXY(point);
    context.lineTo(x * size, y * size);
  }
  context.stroke();
}

function drawArrow(context, anchor, vector, size) {
  if (!anchor || !vector) return;
  const start = [anchor.x * size, anchor.y * size];
  const end = [start[0] + vector.dx * size, start[1] + vector.dy * size];
  const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
  context.beginPath();
  context.moveTo(...start);
  context.lineTo(...end);
  context.stroke();
  const arrowSize = 9;
  context.beginPath();
  context.moveTo(...end);
  context.lineTo(
    end[0] - arrowSize * Math.cos(angle - Math.PI / 6),
    end[1] - arrowSize * Math.sin(angle - Math.PI / 6),
  );
  context.moveTo(...end);
  context.lineTo(
    end[0] - arrowSize * Math.cos(angle + Math.PI / 6),
    end[1] - arrowSize * Math.sin(angle + Math.PI / 6),
  );
  context.stroke();
}

export class CoachOverlay {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.size = 0;
  }

  resize(size, devicePixelRatio = globalThis.devicePixelRatio || 1) {
    this.size = size;
    this.canvas.width = Math.round(size * devicePixelRatio);
    this.canvas.height = Math.round(size * devicePixelRatio);
    this.canvas.style.width = `${size}px`;
    this.canvas.style.height = `${size}px`;
    this.context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    this.clear();
  }

  clear() {
    this.context.clearRect(0, 0, this.size, this.size);
  }

  renderPartial(diagnosis) {
    this.clear();
    if (!diagnosis?.overlay) return;
    const context = this.context;
    context.save();
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = 5;
    context.strokeStyle = "#b42318";
    drawPath(context, diagnosis.overlay.problemSegment, this.size);
    context.setLineDash([7, 6]);
    context.lineWidth = 3;
    context.strokeStyle = "#7a4f01";
    drawPath(context, diagnosis.overlay.targetSegment, this.size);
    context.restore();
  }

  renderResult(diagnosis) {
    this.clear();
    if (!diagnosis) return;
    const context = this.context;
    context.save();
    context.lineCap = "round";
    context.lineJoin = "round";

    if (diagnosis.primaryCue) {
      context.lineWidth = 5;
      context.strokeStyle = diagnosis.accepted ? "#9a6700" : "#b42318";
      drawPath(context, diagnosis.overlay?.problemSegment, this.size);
      context.setLineDash([7, 6]);
      context.lineWidth = 3;
      context.strokeStyle = "#344054";
      drawPath(context, diagnosis.overlay?.targetSegment, this.size);
      context.setLineDash([]);
      context.lineWidth = 3;
      context.strokeStyle = "#175cd3";
      drawArrow(
        context,
        diagnosis.primaryCue.anchor,
        diagnosis.primaryCue.vector,
        this.size,
      );
    }

    const nextStart = diagnosis.accepted ? diagnosis.overlay?.nextStart : null;
    if (nextStart) {
      const x = nextStart.x * this.size;
      const y = nextStart.y * this.size;
      context.fillStyle = "rgba(23, 92, 211, 0.12)";
      context.strokeStyle = "#175cd3";
      context.lineWidth = 3;
      context.beginPath();
      context.arc(x, y, 10, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.fillStyle = "#175cd3";
      context.font = "600 12px sans-serif";
      context.fillText("다음", x + 14, y + 4);
    }
    context.restore();
  }
}

