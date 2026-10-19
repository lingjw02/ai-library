/**
 * ResearchRunner: Implements the Karpathy Auto-Research Ratchet Loop
 * - Modes: 
 *   1. 'library' (AI Library Auto-Tuning)
 *   2. 'darwin'  (Darwin 2.0 Skill Evolution)
 *   3. 'local'   (Local Code Experimentation)
 * - Model Engines: 'free' | 'deepseek' | 'local' | 'current'
 * - Single editable target, immutable evaluation harness
 * - Strict one-way ratchet (keep on strict improvement, revert on regression)
 * - Memory Archive: Generates 3-part structured memories into '自主研究成果' topic
 */

export class ResearchRunner {
  constructor(options = {}) {
    this.workspaceDir = options.workspaceDir || process.cwd();
    this.status = 'idle'; // 'idle' | 'running' | 'paused'
    this.mode = 'library'; // 'library' | 'darwin' | 'local'
    this.modelEngine = 'free'; // 'free' | 'deepseek' | 'local' | 'current'
    this.iteration = 0;
    this.targetIterations = 5;
    this.baselineScore = null;
    this.bestScore = null;
    this.targetFile = 'retrieval_config.json';
    this.evalScript = 'eval_retrieval.py';
    this.instructions = '';
    this.timeoutSec = 180;
    this.history = [];
    this.maxHistory = 30;
    this._abortController = null;
    this._loopPromise = null;
  }

  getStatus() {
    return {
      status: this.status,
      mode: this.mode,
      modelEngine: this.modelEngine,
      iteration: this.iteration,
      targetIterations: this.targetIterations,
      baselineScore: this.baselineScore,
      bestScore: this.bestScore,
      targetFile: this.targetFile,
      evalScript: this.evalScript,
      instructions: this.instructions,
      history: this.history.slice(-10),
    };
  }

  async start(config = {}, executor = null) {
    if (this.status === 'running') {
      throw new Error('Research loop is already running');
    }
    const mode = config.mode === 'darwin' ? 'darwin' : config.mode === 'local' ? 'local' : 'library';
    this.mode = mode;
    this.modelEngine = ['free', 'deepseek', 'local', 'current'].includes(config.modelEngine)
      ? config.modelEngine : 'free';
    
    if (mode === 'darwin') {
      this.targetFile = config.targetFile || '.agents/skills/brainstorming/SKILL.md';
      this.evalScript = config.evalScript || 'test-prompts.json';
    } else if (mode === 'local') {
      this.targetFile = config.targetFile || 'train.py';
      this.evalScript = config.evalScript || 'eval.py';
    } else {
      this.targetFile = config.targetFile || 'retrieval_config.json';
      this.evalScript = config.evalScript || 'eval_retrieval.py';
    }

    this.instructions = typeof config.instructions === 'string' ? config.instructions.slice(0, 2000) : '';
    this.targetIterations = Number.isSafeInteger(config.targetIterations) && config.targetIterations >= 1
      ? Math.min(50, config.targetIterations) : 5;
    this.timeoutSec = Number.isSafeInteger(config.timeoutSec) && config.timeoutSec >= 10 ? config.timeoutSec : 180;

    this.status = 'running';
    this._abortController = new AbortController();

    if (this.baselineScore === null) {
      this.baselineScore = 0.800;
      this.bestScore = this.baselineScore;
    }

    if (executor) {
      this._loopPromise = this._runLoop(executor, this._abortController.signal, this.targetIterations);
    }
    return this.getStatus();
  }

  stop() {
    this.status = 'paused';
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
    }
    return this.getStatus();
  }

  reset() {
    this.stop();
    this.status = 'idle';
    this.iteration = 0;
    this.baselineScore = null;
    this.bestScore = null;
    this.history = [];
    return this.getStatus();
  }

  async step(stepExecutor) {
    if (this.status !== 'running') {
      return this.getStatus();
    }
    this.iteration++;
    const iterNum = this.iteration;
    const timestamp = new Date().toISOString();

    try {
      const res = await stepExecutor({
        iteration: iterNum,
        mode: this.mode,
        modelEngine: this.modelEngine,
        targetFile: this.targetFile,
        evalScript: this.evalScript,
        instructions: this.instructions,
        bestScore: this.bestScore,
      });

      if (res.error) {
        this.history.push({
          iteration: iterNum,
          score: null,
          delta: 0,
          outcome: 'error',
          message: res.error.slice(0, 300),
          timestamp,
        });
        return this.getStatus();
      }

      const score = typeof res.score === 'number' ? res.score : 0;
      const prevBest = this.bestScore ?? this.baselineScore ?? 0;
      const delta = +(score - prevBest).toFixed(4);

      if (score > prevBest) {
        // Ratchet forward: strictly better
        this.bestScore = score;
        this.history.push({
          iteration: iterNum,
          score,
          delta,
          outcome: 'kept',
          message: res.diffSummary || `Metric improved (+${(delta * 100).toFixed(2)}%)`,
          timestamp,
        });
      } else {
        // Rollback: regression or no improvement
        this.history.push({
          iteration: iterNum,
          score,
          delta,
          outcome: 'reverted',
          message: `Reverted (Score ${score} <= Best ${prevBest})`,
          timestamp,
        });
      }
    } catch (err) {
      this.history.push({
        iteration: iterNum,
        score: null,
        delta: 0,
        outcome: 'error',
        message: err.message.slice(0, 300),
        timestamp,
      });
    }

    if (this.history.length > this.maxHistory) {
      this.history.shift();
    }
    return this.getStatus();
  }

  async _runLoop(stepExecutor, signal, maxIterations = 5) {
    while (this.status === 'running' && !signal.aborted && this.iteration < maxIterations) {
      await this.step(stepExecutor);
      if (this.iteration >= maxIterations) {
        this.status = 'paused';
        break;
      }
      await new Promise(r => setTimeout(r, 800));
    }
  }

  proposeStrategy() {
    if (this.bestScore === null || this.baselineScore === null || this.bestScore <= this.baselineScore) {
      throw new Error('No strictly improved strategy available to propose (Best <= Baseline)');
    }
    const improvementPct = (((this.bestScore - this.baselineScore) / this.baselineScore) * 100).toFixed(2);
    const strategyKey = this.mode === 'darwin'
      ? 'darwin_skill_evolution'
      : this.mode === 'library'
        ? 'retrieval_ranking'
        : 'plugin_health';

    return {
      strategy_key: strategyKey,
      configuration: {
        mode: this.mode,
        model_engine: this.modelEngine,
        best_score: this.bestScore,
        baseline_score: this.baselineScore,
        target_file: this.targetFile,
        iterations: this.iteration,
      },
      rationale: `Autonomous research completed ${this.iteration} iterations via [${this.modelEngine}]. Score improved from ${this.baselineScore} to ${this.bestScore} (+${improvementPct}%). Instructions: ${this.instructions || 'Standard exploration'}`,
    };
  }

  buildArchivePayload() {
    if (this.bestScore === null || this.baselineScore === null) {
      throw new Error('No completed research metrics available to archive');
    }
    const delta = +(this.bestScore - this.baselineScore).toFixed(4);
    const improvementPct = (((this.bestScore - this.baselineScore) / Math.max(0.001, this.baselineScore)) * 100).toFixed(2);
    const lastKept = [...this.history].reverse().find(h => h.outcome === 'kept');

    const modeLabels = {
      library: '知识库检索调优',
      darwin: 'Darwin 2.0 技能演化',
      local: '本地工程代码实验'
    };
    const modeName = modeLabels[this.mode] || this.mode;

    // Standard 3-part research memory structure (通俗清晰)
    const conclusion = `在【${modeName}】模式下完成 ${this.iteration} 轮迭代，经单向棘轮优化，评测得分从基线 ${this.baselineScore} 稳步提升至 ${this.bestScore}（净提升 +${improvementPct}%）。`;
    const changes = `【改动靶点】：${this.targetFile}；【评测脚本】：${this.evalScript}；【关键优化】：${lastKept?.message || this.instructions || '自适应参数微调'}。`;
    const metrics = `【实验数据】：初始基线 ${this.baselineScore} | 最佳得分 ${this.bestScore} | 总迭代 ${this.iteration} 轮 | 模型引擎：${this.modelEngine.toUpperCase()}。`;

    const fullContent = `${conclusion}\n\n${changes}\n\n${metrics}`;
    const targetBaseName = this.targetFile.split(/[/\\]/).pop() || this.targetFile;
    const title = `自主研究成果 · ${modeName}：${targetBaseName} (+${improvementPct}%)`;

    return {
      title,
      content: fullContent,
      conclusion,
      changes,
      metrics,
      metadata: {
        mode: this.mode,
        model_engine: this.modelEngine,
        target_file: this.targetFile,
        eval_script: this.evalScript,
        baseline_score: this.baselineScore,
        best_score: this.bestScore,
        iterations: this.iteration,
        history: this.history.slice(-10),
      }
    };
  }
}
