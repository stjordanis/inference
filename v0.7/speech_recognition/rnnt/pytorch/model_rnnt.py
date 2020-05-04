# Copyright (c) 2019, Myrtle Software Limited. All rights reserved.
# Copyright (c) 2019, NVIDIA CORPORATION. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#           http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Optional, Tuple

import numpy as np
import torch

from rnn import rnn
from rnn import StackTime


class RNNT(torch.nn.Module):
    """A Recurrent Neural Network Transducer (RNN-T).

    Args:
        in_features: Number of input features per step per batch.
        vocab_size: Number of output symbols (inc blank).
        forget_gate_bias: Total initialized value of the bias used in the
            forget gate. Set to None to use PyTorch's default initialisation.
            (See: http://proceedings.mlr.press/v37/jozefowicz15.pdf)
        batch_norm: Use batch normalization in encoder and prediction network
            if true.
        encoder_n_hidden: Internal hidden unit size of the encoder.
        encoder_rnn_layers: Encoder number of layers.
        pred_n_hidden:  Internal hidden unit size of the prediction network.
        pred_rnn_layers: Prediction network number of layers.
        joint_n_hidden: Internal hidden unit size of the joint network.
        rnn_type: string. Type of rnn in SUPPORTED_RNNS.
    """

    def __init__(self, rnnt=None, num_classes=1, **kwargs):
        super().__init__()
        if kwargs.get("no_featurizer", False):
            in_features = kwargs.get("in_features")
        else:
            feat_config = kwargs.get("feature_config")
            # This may be useful in the future, for MLPerf
            # configuration.
            in_features = feat_config['features'] * \
                feat_config.get("frame_splicing", 1)

        self._pred_n_hidden = rnnt['pred_n_hidden']

        self.encoder_n_hidden = rnnt["encoder_n_hidden"]
        self.encoder_pre_rnn_layers = rnnt["encoder_pre_rnn_layers"]
        self.encoder_post_rnn_layers = rnnt["encoder_post_rnn_layers"]

        self.pred_n_hidden = rnnt["pred_n_hidden"]
        self.pred_rnn_layers = rnnt["pred_rnn_layers"]

        self.encoder_pre_rnn, self.encoder_stack_time, self.encoder_post_rnn = self._encoder(
            in_features,
            rnnt["encoder_n_hidden"],
            rnnt["encoder_pre_rnn_layers"],
            rnnt["encoder_post_rnn_layers"],
            rnnt["forget_gate_bias"],
            None if "norm" not in rnnt else rnnt["norm"],
            rnnt["rnn_type"],
            rnnt["encoder_stack_time_factor"],
            rnnt["dropout"],
        )
        # self.encoder_pre_rnn = torch.jit.ignore(self.encoder_pre_rnn)
        # self.encoder_stack_time = torch.jit.ignore(self.encoder_stack_time)
        # self.encoder_post_rnn = torch.jit.ignore(self.encoder_post_rnn)

        self.prediction_embed, self.prediction_dec_rnn = self._predict(
            num_classes,
            rnnt["pred_n_hidden"],
            rnnt["pred_rnn_layers"],
            rnnt["forget_gate_bias"],
            # Note: This is clearly an error
            None if "norm" not in "rnnt" else rnnt["norm"],
            rnnt["rnn_type"],
            rnnt["dropout"],
        )

        self.joint_net = self._joint_net(
            num_classes,
            rnnt["pred_n_hidden"],
            rnnt["encoder_n_hidden"],
            rnnt["joint_n_hidden"],
            rnnt["dropout"],
        )

    def _encoder(self, in_features, encoder_n_hidden,
                 encoder_pre_rnn_layers, encoder_post_rnn_layers,
                 forget_gate_bias, norm, rnn_type, encoder_stack_time_factor,
                 dropout):
        return (
            rnn(
                rnn=rnn_type,
                input_size=in_features,
                hidden_size=encoder_n_hidden,
                num_layers=encoder_pre_rnn_layers,
                norm=norm,
                forget_gate_bias=forget_gate_bias,
                dropout=dropout,
            ),
            StackTime(factor=encoder_stack_time_factor),
            rnn(
                rnn=rnn_type,
                input_size=encoder_stack_time_factor * encoder_n_hidden,
                hidden_size=encoder_n_hidden,
                num_layers=encoder_post_rnn_layers,
                norm=norm,
                forget_gate_bias=forget_gate_bias,
                norm_first_rnn=True,
                dropout=dropout,
            ),
        )

    def _predict(self, vocab_size, pred_n_hidden, pred_rnn_layers,
                 forget_gate_bias, norm, rnn_type, dropout):
        return (torch.nn.Embedding(vocab_size - 1, pred_n_hidden),
                rnn(
                    rnn=rnn_type,
                    input_size=pred_n_hidden,
                    hidden_size=pred_n_hidden,
                    num_layers=pred_rnn_layers,
                    norm=norm,
                    forget_gate_bias=forget_gate_bias,
                    dropout=dropout,
                )
        )

    def _joint_net(self, vocab_size, pred_n_hidden, enc_n_hidden,
                   joint_n_hidden, dropout):
        layers = [
            torch.nn.Linear(pred_n_hidden + enc_n_hidden, joint_n_hidden),
            torch.nn.ReLU(),
        ] + ([torch.nn.Dropout(p=dropout), ] if dropout else []) + [
            torch.nn.Linear(joint_n_hidden, vocab_size)
        ]
        return torch.nn.Sequential(
            *layers
        )

    # Perhaps what I really need to do is provide a value for
    # state. But why can't I just specify a type for abstract
    # intepretation? That's what I really want!
    # We really want two "states" here...
    @torch.jit.unused
    def forward(self, x_padded, x_lens, y_packed, y_lens,
                state: Optional[Tuple[torch.Tensor, torch.Tensor]] = None):
        # batch: ((x, y), (x_lens, y_lens))

        raise RuntimeError(
            "RNNT::forward is not currently used. "
            "It corresponds to training, where your entire output sequence "
            "is known before hand.")
        # x: TxBxF
        f, x_lens = self.encode(x_padded, x_lens)

        g, _ = self.predict(y_packed, state)
        out = self.joint(f, g)

        return out, (x_lens, y_lens)

    # @torch.jit.ignore
    # @torch.jit.export
    def apply_encode_pre_rnn(self, x_padded):
        x_padded, _ = self.encoder_pre_rnn(x_padded, None)
        return x_padded

    # @torch.jit.ignore
    # @torch.jit.export
    def apply_encode_post_rnn(self, x_padded):
        x_padded, _ = self.encoder_post_rnn(x_padded, None)
        return x_padded

    @torch.jit.export
    def encode(self, x_padded: torch.Tensor, x_lens: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: tuple of ``(input, input_lens)``. ``input`` has shape (T, B, I),
                ``input_lens`` has shape ``(B,)``.

        Returns:
            f: tuple of ``(output, output_lens)``. ``output`` has shape
                (B, T, H), ``output_lens``
        """
        x_padded = self.apply_encode_pre_rnn(x_padded)
        x_padded, x_lens = self.encoder_stack_time(x_padded, x_lens)
        # (T, B, H)
        x_padded = self.apply_encode_post_rnn(x_padded)
        # (B, T, H)
        # x_padded = x_padded.transpose(0, 1)
        x_padded = self._encode_transpose(x_padded)
        return x_padded, x_lens

    @torch.jit.ignore
    def _encode_transpose(self, x_padded):
        return x_padded.transpose(0, 1)

    # @torch.jit.ignore
    @torch.jit.export
    def predict(self, y: Optional[torch.Tensor],
                state: Optional[Tuple[torch.Tensor, torch.Tensor]] = None) -> Tuple[torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        """
        B - batch size
        U - label length
        H - Hidden dimension size
        L - Number of decoder layers = 2

        Args:
            y: (B, U)

        Returns:
            Tuple (g, hid) where:
                g: (B, U + 1, H)
                hid: (h, c) where h is the final sequence hidden state and c is
                    the final cell state:
                        h (tensor), shape (L, B, H)
                        c (tensor), shape (L, B, H)
        """
        if y is None:
            # This is gross. I should really just pass in an SOS token
            # instead. Is there no SOS token?
            assert state is None
            # Hacky, no way to determine this right now!
            B = 1
            y = torch.zeros((B, 1, self.pred_n_hidden), dtype=torch.float32)
        else:
            y = self.prediction_embed(y)

        # if state is None:
        #    batch = y.size(0)
        #    state = [
        #        (torch.zeros(batch, self.pred_n_hidden, dtype=y.dtype, device=y.device),
        #         torch.zeros(batch, self.pred_n_hidden, dtype=y.dtype, device=y.device))
        #        for _ in range(self.pred_rnn_layers)
        #    ]

        y = y.transpose(0, 1)  # .contiguous()   # (U + 1, B, H)
        g, hid = self.prediction_dec_rnn(y, state)
        g = g.transpose(0, 1)  # .contiguous()   # (B, U + 1, H)
        # del y, state
        return g, hid

    @torch.jit.export
    def joint(self, f: torch.Tensor, g: torch.Tensor):
        """
        f should be shape (B, T, H)
        g should be shape (B, U + 1, H)

        returns:
            logits of shape (B, T, U, K + 1)
        """
        # Combine the input states and the output states
        B, T, H = f.shape
        B, U_, H2 = g.shape

        f = f.unsqueeze(dim=2)   # (B, T, 1, H)
        f = f.expand((B, T, U_, H))

        g = g.unsqueeze(dim=1)   # (B, 1, U + 1, H)
        g = g.expand((B, T, U_, H2))

        inp = torch.cat([f, g], dim=3)   # (B, T, U, 2H)
        res = self.joint_net(inp)
        # del f, g, inp
        return res


def label_collate(labels):
    """Collates the label inputs for the rnn-t prediction network.

    If `labels` is already in torch.Tensor form this is a no-op.

    Args:
        labels: A torch.Tensor List of label indexes or a torch.Tensor.

    Returns:
        A padded torch.Tensor of shape (batch, max_seq_len).
    """

    if isinstance(labels, torch.Tensor):
        return labels.type(torch.int64)
    if not isinstance(labels, (list, tuple)):
        raise ValueError(
            f"`labels` should be a list or tensor not {type(labels)}"
        )

    batch_size = len(labels)
    max_len = max(len(l) for l in labels)

    cat_labels = np.full((batch_size, max_len), fill_value=0.0, dtype=np.int32)
    for e, l in enumerate(labels):
        cat_labels[e, :len(l)] = l
    labels = torch.LongTensor(cat_labels)

    return labels
