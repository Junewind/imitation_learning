import os
import numpy as np
import torch
from tqdm import trange
from tensorboardX import SummaryWriter


# TODO: add logging here
class OnPolicyTrainer:
    """
    Simple on-policy trainer.
    """
    def __init__(
            self,
            agent, train_env, test_env,
            log_dir
    ):
        self._agent = agent
        # both environments should:
        #   vectorized
        #   reset environment automatically
        self._train_env = train_env
        self._test_env = test_env
        # store episode reward and number for each train environment
        self._env_reward = np.zeros(train_env.num_envs, dtype=np.float32)
        self._env_episode = np.zeros(train_env.num_envs, dtype=np.int32)

        self._log_dir = log_dir

        try:
            os.mkdir(self._log_dir)
            os.mkdir(self._log_dir + 'tb')
            os.mkdir(self._log_dir + 'checkpoints')
        except FileExistsError:
            print('log_dir already exists')

        # tensorboard logs saved in 'log_dir/tb/', checkpoints in 'log_dir/checkpoints'
        self._writer = SummaryWriter(log_dir + 'tb/')  # instantiate this

    def _gather_rollout(self, observation, rollout_len):
        observations, actions, rewards, is_done = [observation], [], [], []
        for _ in range(rollout_len):
            # on-policy trainer does not requires actions to be differentiable
            # however, agent may be used by different algorithms which may require that
            with torch.no_grad():
                action = self._agent.act(observation)
            action = action.cpu().numpy()
            observation, reward, done, _ = self._train_env.step(action)

            observations.append(observation)
            actions.append(action)
            rewards.append(reward)
            is_done.append(done)

            self._env_reward += reward
            self._done(done)

        rollout = observations, actions, rewards, is_done
        return rollout, observation

    def _done(self, done):
        # TODO: better name?
        if np.any(done):
            for i, d in enumerate(done):
                if d:
                    self._write_logs(
                        f'agents/agent_{i}/',
                        {'reward': self._env_reward[i]},
                        self._env_episode[i]
                    )
                    self._env_reward[i] = 0
                    self._env_episode[i] += 1

    def _write_logs(self, tag, values, step):
        for key, value in values.items():
            self._writer.add_scalar(tag + key, value, step)

    def _train_step(self, observation, rollout_len, step):
        # gather rollout -> train on it -> write training logs
        rollout, observation = self._gather_rollout(observation, rollout_len)
        train_logs = self._agent.train_on_rollout(rollout)
        self._write_logs('train/', train_logs, step)
        return observation

    def _test_agent(self, step, n_tests):
        n_test_envs = self._test_env.num_envs
        observation = self._test_env.reset()
        env_reward = np.zeros(n_test_envs, dtype=np.float32)
        episode_rewards = []

        while len(episode_rewards) < n_tests:
            with torch.no_grad():
                action = self._agent.act(observation, deterministic=True)
            action = action.cpu().numpy()
            observation, reward, done, _ = self._test_env.step(action)
            env_reward += reward
            if np.any(done):
                for i, d in enumerate(done):
                    if d:
                        episode_rewards.append(env_reward[i])
                        env_reward[i] = 0.0

        reward_mean = np.mean(episode_rewards)
        reward_std = np.std(episode_rewards)
        write_dict = {
            'reward_mean': reward_mean,
            'reward_std': reward_std
        }
        self._write_logs('test/', write_dict, step)

    def train(self, n_epoch, n_steps, rollout_len, n_tests):
        """
        Run training for 'n_epoch', each epoch takes 'n_steps' training steps
        on rollouts of len 'rollout_len'.
        At the end of each epoch run 'n_tests' tests and saves checkpoint

        :param n_epoch:
        :param n_steps:
        :param rollout_len:
        :param n_tests:
        :return:
        """
        observation = self._train_env.reset()
        self._test_agent(0, n_tests)

        for epoch in range(n_epoch):
            p_bar = trange(n_steps, ncols=90, desc=f'epoch_{epoch}')
            for train_step in p_bar:
                observation = self._train_step(
                    observation, rollout_len, train_step + epoch * n_steps
                )
            # TODO: for some reason tensorboard does not contain test reward after last epoch.
            #  Need to figure out why and fix
            self._test_agent(epoch + 1, n_tests)
            self._agent.save(self._log_dir + 'checkpoints/' + f'epoch_{epoch}.pth')
