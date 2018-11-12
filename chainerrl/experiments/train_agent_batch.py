from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import
from future import standard_library
standard_library.install_aliases()  # NOQA

from collections import deque
import logging

import numpy as np


from chainerrl.experiments.evaluator import Evaluator
from chainerrl.experiments.evaluator import save_agent
from chainerrl.misc.makedirs import makedirs


def train_agent_batch(agent, env, steps, outdir, log_interval=None,
                      max_episode_len=None, eval_interval=None,
                      step_offset=0, evaluator=None, successful_score=None,
                      step_hooks=[], return_window_size=100, logger=None):
    """Train an agent in a batch environment.

    Args:
        agent: Agent to train.
        env: Environment train the againt against.
        steps (int): Number of total time steps for training.
        eval_interval (int): Interval of evaluation.
        outdir (str): Path to the directory to output things.
        log_interval (int): Interval of logging.
        max_episode_len (int): Maximum episode length.
        step_offset (int): Time step from which training starts.
        return_window_size (int): Number of training episodes used to estimate
            the average returns of the current agent.
        successful_score (float): Finish training if the mean score is greater
            or equal to thisvalue if not None
        step_hooks (list): List of callable objects that accepts
            (env, agent, step) as arguments. They are called every step.
            See chainerrl.experiments.hooks.
        logger (logging.Logger): Logger used in this function.
    """

    logger = logger or logging.getLogger(__name__)
    recent_returns = deque(maxlen=return_window_size)

    num_envs = env.num_envs
    episode_r = np.zeros(num_envs, dtype=np.float64)
    episode_idx = np.zeros(num_envs, dtype='i')
    episode_len = np.zeros(num_envs, dtype='i')

    # o_0, r_0
    obss = env.reset()
    rs = np.zeros(num_envs, dtype='f')

    t = step_offset
    if hasattr(agent, 't'):
        agent.t = step_offset

    try:
        while t < steps:
            # a_t
            actions = agent.batch_act_and_train(obss)
            # o_{t+1}, r_{t+1}
            obss, rs, dones, infos = env.step(actions)
            episode_r += rs
            episode_len += 1

            # Compute mask for done and reset
            if max_episode_len is None:
                resets = np.zeros(num_envs, dtype=bool)
            else:
                resets = (episode_len == max_episode_len)
            # Agent observes the consequences
            agent.batch_observe_and_train(obss, rs, dones, resets)

            # Make mask. 0 if done/reset, 1 if pass
            end = np.logical_or(resets, dones)
            not_end = np.logical_not(end)

            # For episodes that ends, do the following:
            #   1. increment the episode count
            #   2. record the return
            #   3. clear the record of rewards
            #   4. clear the record of the number of steps
            #   5. reset the env to start a new episode
            episode_idx += end
            recent_returns.extend(episode_r[end])
            episode_r[end] = 0
            episode_len[end] = 0
            obss = env.reset(not_end)

            for _ in range(num_envs):
                t += 1
                for hook in step_hooks:
                    hook(env, agent, t)

            if (log_interval is not None
                    and t >= log_interval
                    and t % log_interval < num_envs):
                logger.info(
                    'outdir:{} step:{} episode:{} last_R: {} average_R:{}'.format(  # NOQA
                        outdir,
                        t,
                        np.sum(episode_idx),
                        recent_returns[-1] if recent_returns else np.nan,
                        np.mean(recent_returns) if recent_returns else np.nan,
                    ))
                logger.info('statistics: {}'.format(agent.get_statistics()))
            if evaluator:
                if evaluator.evaluate_if_necessary(
                        t=t, episodes=np.sum(episode_idx)):
                    if (successful_score is not None and
                            evaluator.max_score >= successful_score):
                        break

    except (Exception, KeyboardInterrupt):
        # Save the current model before being killed
        save_agent(agent, t, outdir, logger, suffix='_except')
        env.close()
        if evaluator:
            evaluator.env.close()
        raise
    else:
        # Save the final model
        save_agent(agent, t, outdir, logger, suffix='_finish')


def train_agent_batch_with_evaluation(agent,
                                      env,
                                      steps,
                                      eval_n_runs,
                                      eval_interval,
                                      outdir,
                                      max_episode_len=None,
                                      step_offset=0,
                                      eval_max_episode_len=None,
                                      return_window_size=100,
                                      eval_env=None,
                                      log_interval=None,
                                      successful_score=None,
                                      step_hooks=[],
                                      save_best_so_far_agent=True,
                                      logger=None,
                                      ):
    """Train an agent while regularly evaluating it.

    Args:
        agent: Agent to train.
        env: Environment train the againt against.
        steps (int): Number of total time steps for training.
        eval_n_runs (int): Number of runs for each time of evaluation.
        eval_interval (int): Interval of evaluation.
        outdir (str): Path to the directory to output things.
        log_interval (int): Interval of logging.
        max_episode_len (int): Maximum episode length.
        step_offset (int): Time step from which training starts.
        return_window_size (int): Number of training episodes used to estimate
            the average returns of the current agent.
        eval_max_episode_len (int or None): Maximum episode length of
            evaluation runs. If set to None, max_episode_len is used instead.
        eval_env: Environment used for evaluation.
        successful_score (float): Finish training if the mean score is greater
            or equal to thisvalue if not None
        step_hooks (list): List of callable objects that accepts
            (env, agent, step) as arguments. They are called every step.
            See chainerrl.experiments.hooks.
        save_best_so_far_agent (bool): If set to True, after each evaluation,
            if the score (= mean return of evaluation episodes) exceeds
            the best-so-far score, the current agent is saved.
        logger (logging.Logger): Logger used in this function.
    """

    logger = logger or logging.getLogger(__name__)

    makedirs(outdir, exist_ok=True)

    if eval_env is None:
        eval_env = env

    if eval_max_episode_len is None:
        eval_max_episode_len = max_episode_len

    evaluator = Evaluator(agent=agent,
                          n_runs=eval_n_runs,
                          eval_interval=eval_interval, outdir=outdir,
                          max_episode_len=eval_max_episode_len,
                          env=eval_env,
                          step_offset=step_offset,
                          save_best_so_far_agent=save_best_so_far_agent,
                          logger=logger,
                          )

    train_agent_batch(
        agent, env, steps, outdir,
        max_episode_len=max_episode_len,
        step_offset=step_offset,
        eval_interval=eval_interval,
        evaluator=evaluator,
        successful_score=successful_score,
        return_window_size=return_window_size,
        log_interval=log_interval,
        step_hooks=step_hooks,
        logger=logger)
